"""Azure compute model and API registration coverage."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.infra.compute import (
    ComputeProvider,
    GPUType,
    InferenceEngine,
)
from general_ludd.routers import compute as compute_router
from general_ludd.routers.compute import register


def _inject_admin_auth(app):
    from general_ludd.security.permissions import (
        Capability,
        PermissionSpec,
        PermissionSubject,
    )

    spec = PermissionSpec(
        agent_type="admin-test",
        capabilities=[Capability(resource="admin:compute", actions=["destroy"], constraints={})],
        subject=PermissionSubject.HUMAN,
    )

    async def _attach_auth_spec(request, call_next):
        request.state.auth_spec = spec
        return await call_next(request)

    app.middleware("http")(_attach_auth_spec)


def test_azure_a100_shapes_are_public_compute_values() -> None:
    assert ComputeProvider("azure") is ComputeProvider.AZURE
    assert GPUType("a100_40") is GPUType.A100_40
    assert GPUType("a100_80") is GPUType.A100_80


def test_compute_router_registers_read_only_azure_preflight() -> None:
    app = FastAPI()
    register(app, {})

    routes = {(route.path, frozenset(route.methods or set())) for route in app.routes}
    assert (
        "/admin/compute/azure/preflight",
        frozenset({"POST"}),
    ) in routes


def test_compute_endpoint_lifecycle_reports_real_utilization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    util = MagicMock()
    endpoint = SimpleNamespace(
        endpoint_id="gpu-east",
        url="https://gpu-east.example/v1",
        model="org/model",
        utilization=0.25,
        current_load=1,
        max_concurrent=4,
        available_slots=3,
        active=True,
    )
    util.get_utilization_report.return_value = {
        "total_endpoints": 1,
        "active_endpoints": 1,
    }
    util.list_endpoints.return_value = [endpoint]
    util.register_endpoint.return_value = endpoint
    monkeypatch.setattr(
        compute_router,
        "_get_or_create_extended_subsystems",
        lambda _app: {"utilization": util},
    )
    app = FastAPI()
    register(app, {})
    app.state.daemon_state = {
        "idle_endpoints": {"gpu-east": {"endpoint_id": "gpu-east"}},
        "torn_down_endpoints": ["gpu-old"],
    }
    client = TestClient(app)

    utilization = client.get("/admin/compute/utilization")
    endpoints = client.get("/admin/compute/endpoints")
    invalid = client.post(
        "/admin/compute/endpoints",
        json={"endpoint_id": "missing-url"},
    )
    created = client.post(
        "/admin/compute/endpoints",
        json={
            "endpoint_id": "gpu-east",
            "url": "https://gpu-east.example/v1",
            "model": "org/model",
            "gpu_type": "a100_80",
            "gpu_count": 2,
            "max_concurrent": 4,
        },
    )
    idle = client.get("/admin/compute/idle")
    removed = client.delete("/admin/compute/endpoints/gpu-east")

    assert utilization.json() == {
        "total_endpoints": 1,
        "active_endpoints": 1,
    }
    assert endpoints.json() == {
        "endpoints": [
            {
                "endpoint_id": "gpu-east",
                "url": "https://gpu-east.example/v1",
                "model": "org/model",
                "utilization_pct": 25.0,
                "current_load": 1,
                "max_concurrent": 4,
                "available_slots": 3,
                "active": True,
            }
        ]
    }
    assert invalid.status_code == 422
    assert created.json() == {
        "endpoint_id": "gpu-east",
        "url": "https://gpu-east.example/v1",
        "model": "org/model",
    }
    util.register_endpoint.assert_called_once_with(
        endpoint_id="gpu-east",
        url="https://gpu-east.example/v1",
        model="org/model",
        gpu_type="a100_80",
        gpu_count=2,
        max_concurrent=4,
    )
    assert idle.json() == {
        "idle_endpoints": [{"endpoint_id": "gpu-east"}],
        "torn_down_endpoints": ["gpu-old"],
    }
    assert removed.json() == {"removed": "gpu-east"}
    util.unregister_endpoint.assert_called_once_with("gpu-east")


@pytest.mark.parametrize(
    ("failure", "expected_status", "expected_detail"),
    [
        (
            RuntimeError("credentials unavailable"),
            503,
            "Azure accelerator preflight unavailable",
        ),
        (
            OSError("Azure API disconnected"),
            502,
            "Azure accelerator preflight failed",
        ),
    ],
)
def test_azure_preflight_translates_provider_failures(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_status: int,
    expected_detail: str,
) -> None:
    check = MagicMock(side_effect=failure)
    monkeypatch.setattr(
        compute_router,
        "resolve_accelerator",
        MagicMock(),
    )
    monkeypatch.setattr(
        compute_router,
        "build_default_azure_preflight",
        MagicMock(return_value=SimpleNamespace(check=check)),
    )
    app = FastAPI()
    register(app, {})

    response = TestClient(app).post(
        "/admin/compute/azure/preflight",
        json={
            "gpu_type": "a100_80",
            "gpu_count": 2,
            "region": "eastus2",
        },
    )

    assert response.status_code == expected_status
    assert response.json()["detail"] == expected_detail
    check.assert_called_once_with(
        gpu_type=GPUType.A100_80,
        gpu_count=2,
        location="eastus2",
    )


def test_deploy_rejects_unknown_values_and_defaults_unknown_engine() -> None:
    instance = SimpleNamespace(
        instance_id="i-default-engine",
        provider=ComputeProvider.AWS,
        status="running",
        ip_address="192.0.2.10",
        port=8000,
        gpu_type=GPUType.A10,
        endpoint_url=None,
    )
    manager = MagicMock()
    manager.deploy = AsyncMock(return_value=instance)
    app = FastAPI()
    register(app, {})
    app.state._deployment_manager = manager
    client = TestClient(app)

    unknown_gpu = client.post(
        "/admin/compute/deploy",
        json={
            "provider": "aws",
            "gpu_type": "not-a-gpu",
            "model_name": "org/model",
        },
    )
    unknown_provider = client.post(
        "/admin/compute/deploy",
        json={
            "provider": "not-a-provider",
            "gpu_type": "a10",
            "model_name": "org/model",
        },
    )
    default_engine = client.post(
        "/admin/compute/deploy",
        json={
            "provider": "aws",
            "gpu_type": "a10",
            "model_name": "org/model",
            "engine": "not-an-engine",
        },
    )

    assert unknown_gpu.status_code == 422
    assert unknown_gpu.json()["detail"] == "Unknown GPU type: not-a-gpu"
    assert unknown_provider.status_code == 422
    assert unknown_provider.json()["detail"] == ("Unknown provider: not-a-provider")
    assert default_engine.status_code == 200
    deployed_config = manager.deploy.await_args.args[0]
    assert deployed_config.engine is InferenceEngine.VLLM


def test_compute_destroy_removes_records_and_translates_provider_failure() -> None:
    manager = MagicMock()
    manager.get_deployment_shared = AsyncMock(return_value=object())
    manager.destroy = AsyncMock()
    app = FastAPI()
    register(app, {})
    _inject_admin_auth(app)
    app.state._deployment_manager = manager
    app.state._compute_deployments = {
        "i-complete": object(),
        "i-failed": object(),
    }
    client = TestClient(app)

    destroyed = client.delete("/admin/compute/destroy/i-complete")
    manager.destroy.side_effect = RuntimeError("terraform destroy failed")
    failed = client.delete("/admin/compute/destroy/i-failed")

    assert destroyed.json() == {"destroyed": "i-complete"}
    assert "i-complete" not in app.state._compute_deployments
    assert failed.status_code == 500
    assert failed.json()["detail"] == "compute destroy failed"
    assert "i-failed" in app.state._compute_deployments


def test_gpu_metric_routes_serialize_dict_object_and_unassigned_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint_a = SimpleNamespace(endpoint_id="gpu-a")
    endpoint_b = SimpleNamespace(endpoint_id="gpu-b")
    object_metric = SimpleNamespace(
        gpu_sm_util_pct=42.0,
        gpu_mem_util_pct=35.0,
        gpu_temp_c=61.0,
        power_draw_w=225.0,
        memory_used_mb=4096.0,
        memory_total_mb=81920.0,
    )
    dict_metric = {
        "gpu_sm_util_pct": 70.0,
        "gpu_mem_util_pct": 50.0,
    }
    util = MagicMock()
    util.list_endpoints.return_value = [endpoint_a]
    monkeypatch.setattr(
        compute_router,
        "_get_or_create_extended_subsystems",
        lambda _app: {"utilization": util},
    )
    app = FastAPI()
    register(app, {})
    app.state.daemon_state = {
        "_last_gpu_metrics": [dict_metric, object_metric],
        "_last_gpu_metrics_at": 123.5,
    }
    client = TestClient(app)

    all_metrics = client.get("/admin/compute/gpu-metrics")
    util.list_endpoints.return_value = [endpoint_a, endpoint_b]
    dict_response = client.get("/admin/compute/gpu-metrics/gpu-a")
    object_response = client.get("/admin/compute/gpu-metrics/gpu-b")
    missing = client.get("/admin/compute/gpu-metrics/unknown")

    assert all_metrics.json() == {
        "metrics": {
            "gpu-a": dict_metric,
            "device_1": {
                "gpu_sm_util_pct": 42.0,
                "gpu_mem_util_pct": 35.0,
                "gpu_temp_c": 61.0,
                "power_draw_w": 225.0,
                "memory_used_mb": 4096.0,
                "memory_total_mb": 81920.0,
            },
        },
        "collected_at": 123.5,
    }
    assert dict_response.json() == {
        "endpoint_id": "gpu-a",
        "metrics": dict_metric,
    }
    assert object_response.json() == {
        "endpoint_id": "gpu-b",
        "metrics": {
            "gpu_sm_util_pct": 42.0,
            "gpu_mem_util_pct": 35.0,
            "gpu_temp_c": 61.0,
            "power_draw_w": 225.0,
            "memory_used_mb": 4096.0,
            "memory_total_mb": 81920.0,
        },
    }
    assert missing.status_code == 404


def test_azure_deploy_refuses_spend_when_preflight_is_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preflight_body = {
        "ready": False,
        "sku_available": True,
        "regional_quota_available": False,
    }
    preflight_result = SimpleNamespace(
        ready=False,
        as_dict=MagicMock(return_value=preflight_body),
    )
    preflight_check = MagicMock(return_value=preflight_result)
    monkeypatch.setattr(
        compute_router,
        "resolve_accelerator",
        MagicMock(),
    )
    monkeypatch.setattr(
        compute_router,
        "build_default_azure_preflight",
        MagicMock(
            return_value=SimpleNamespace(check=preflight_check),
        ),
    )
    manager = MagicMock()
    manager.deploy = AsyncMock()
    app = FastAPI()
    register(app, {})
    app.state._deployment_manager = manager

    response = TestClient(app).post(
        "/admin/compute/deploy",
        json={
            "provider": "azure",
            "gpu_type": "a100_80",
            "gpu_count": 2,
            "model_name": "org/model",
            "region": "eastus2",
            "subscription_id": "subscription-id",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "error": "Azure accelerator preflight blocked deployment",
        "preflight": preflight_body,
    }
    preflight_check.assert_called_once_with(
        gpu_type=GPUType.A100_80,
        gpu_count=2,
        location="eastus2",
    )
    manager.deploy.assert_not_awaited()


def test_deploy_failure_records_health_checker_context() -> None:
    manager = MagicMock()
    manager.deploy = AsyncMock(side_effect=RuntimeError("provider failed"))
    checker = MagicMock()
    app = FastAPI()
    register(app, {})
    app.state._deployment_manager = manager
    app.state._deployment_health_router = SimpleNamespace(
        health_checker=checker,
    )

    response = TestClient(app).post(
        "/admin/compute/deploy",
        json={
            "provider": "aws",
            "gpu_type": "a10",
            "model_name": "org/model",
        },
    )

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "error": "compute deploy failed",
        "misconfig_findings": [],
    }
    checker.record_failure.assert_called_once()
    args, kwargs = checker.record_failure.call_args
    assert args[0] == "org/model"
    assert isinstance(args[1], RuntimeError)
    assert kwargs == {"kind": "deploy_failure"}


def test_failed_endpoint_registration_rolls_back_paid_deployment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instance = SimpleNamespace(
        instance_id="i-unregistered",
        provider=ComputeProvider.AWS,
        status="running",
        ip_address="192.0.2.20",
        port=8000,
        gpu_type=GPUType.A10,
        endpoint_url="https://gpu.example/v1",
    )
    manager = MagicMock()
    manager.deploy = AsyncMock(return_value=instance)
    manager.destroy = AsyncMock()
    util = MagicMock()
    util.register_endpoint.side_effect = RuntimeError(
        "scheduler registry unavailable",
    )
    monkeypatch.setattr(
        compute_router,
        "_get_or_create_extended_subsystems",
        lambda _app: {"utilization": util},
    )
    app = FastAPI()
    register(app, {})
    app.state._deployment_manager = manager

    response = TestClient(app).post(
        "/admin/compute/deploy",
        json={
            "provider": "aws",
            "gpu_type": "a10",
            "model_name": "org/model",
        },
    )

    assert response.status_code == 500
    assert response.json()["detail"] == ("compute endpoint registration failed; deployment rolled back")
    manager.destroy.assert_awaited_once_with("i-unregistered")
    assert "i-unregistered" not in app.state._compute_deployments


def test_deployment_listing_lazily_constructs_and_caches_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_at = datetime(2026, 7, 29, 12, 30, tzinfo=UTC)
    manager = MagicMock()
    manager.list_deployments_shared = AsyncMock(
        return_value=[
            SimpleNamespace(
                instance_id="i-persisted",
                provider="aws",
                model_name="org/model",
                state="running",
                ip_address="192.0.2.30",
                endpoint_url="https://gpu.example/v1",
                working_dir="/tmp/gludd-compute-i-persisted",
                created_at=created_at,
            )
        ]
    )
    manager_factory = MagicMock(return_value=manager)
    mkdir = MagicMock()
    monkeypatch.setattr(compute_router, "DeploymentManager", manager_factory)
    monkeypatch.setattr(compute_router.os, "makedirs", mkdir)
    monkeypatch.setattr(
        compute_router.os.path,
        "expanduser",
        lambda _path: "/tmp/gludd-compute-tests",
    )
    resolver = object()
    app = FastAPI()
    register(app, {})
    app.state._secrets_resolver = resolver
    client = TestClient(app)

    first = client.get("/api/deployments")
    second = client.get("/api/deployments")

    expected = {
        "deployments": [
            {
                "instance_id": "i-persisted",
                "provider": "aws",
                "model_name": "org/model",
                "state": "running",
                "ip_address": "192.0.2.30",
                "endpoint_url": "https://gpu.example/v1",
                "working_dir": "/tmp/gludd-compute-i-persisted",
                "created_at": created_at.isoformat(),
            }
        ],
        "count": 1,
    }
    assert first.json() == expected
    assert second.json() == expected
    assert manager_factory.call_count == 1
    call = manager_factory.call_args
    assert call is not None
    assert call.kwargs["secrets_resolver"] is resolver
    assert call.kwargs["working_dir"] == "/tmp/gludd-compute-tests/deployments"
    assert call.kwargs["session_factory"] is None
    assert call.kwargs["worker_id"].startswith("router-")
    mkdir.assert_called_once_with(
        "/tmp/gludd-compute-tests/deployments",
        exist_ok=True,
    )
