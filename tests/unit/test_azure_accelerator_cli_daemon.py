"""CLI and daemon contracts for Azure accelerator preflight."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd import cli
from general_ludd.infra.azure_accelerator import AzurePreflightResult
from general_ludd.infra.compute import ComputeInstance, ComputeProvider, GPUType
from general_ludd.routers import compute


def _ready_result() -> AzurePreflightResult:
    return AzurePreflightResult(
        ready=True,
        location="eastus",
        gpu_type=GPUType.H100,
        gpu_count=1,
        vm_size="Standard_NC40ads_H100_v5",
        requested_vcpus=40,
        sku_available=True,
        family_quota_remaining=80,
        regional_quota_remaining=120,
        blockers=(),
        warnings=("capacity is not guaranteed",),
    )


def test_compute_cli_exposes_azure_preflight_command() -> None:
    parser, _ = cli.build_parser()
    args = parser.parse_args(
        [
            "compute",
            "azure-preflight",
            "--gpu",
            "h100",
            "--gpu-count",
            "1",
            "--region",
            "eastus",
        ]
    )
    assert args.func is cli._cmd_compute_azure_preflight


def test_compute_cli_posts_azure_preflight_payload() -> None:
    parser, _ = cli.build_parser()
    args = parser.parse_args(
        [
            "compute",
            "azure-preflight",
            "--gpu",
            "a100_80",
            "--gpu-count",
            "2",
            "--region",
            "eastus",
        ]
    )
    with patch.object(cli, "_http_call", return_value={"ready": True}) as request:
        cli._cmd_compute_azure_preflight(args)
    request.assert_called_once()
    assert request.call_args.kwargs["json"] == {
        "gpu_type": "a100_80",
        "gpu_count": 2,
        "region": "eastus",
    }


def test_compute_launch_exposes_runtime_storage_and_image_controls() -> None:
    parser, _ = cli.build_parser()
    args = parser.parse_args(
        [
            "compute",
            "launch",
            "--provider",
            "azure",
            "--gpu",
            "a100_80",
            "--model",
            "org/model",
            "--timeout-minutes",
            "180",
            "--disk-size-gb",
            "512",
            "--container-image",
            "registry.example/gludd-vllm:stable",
        ]
    )
    with patch.object(cli, "_http_call", return_value={"state": "running"}) as request:
        cli._cmd_compute_launch(args)

    payload = request.call_args.kwargs["json"]
    assert payload["timeout_minutes"] == 180
    assert payload["disk_size_gb"] == 512
    assert payload["container_image"] == "registry.example/gludd-vllm:stable"


def test_daemon_exposes_read_only_azure_preflight() -> None:
    app = FastAPI()
    compute.register(app, {})
    with (
        patch(
            "general_ludd.routers.compute.build_default_azure_preflight"
        ) as build,
        TestClient(app) as client,
    ):
        build.return_value.check.return_value = _ready_result()
        response = client.post(
            "/admin/compute/azure/preflight",
            json={
                "gpu_type": "h100",
                "gpu_count": 1,
                "region": "eastus",
            },
        )
    assert response.status_code == 200
    assert response.json()["ready"] is True
    assert response.json()["vm_size"] == "Standard_NC40ads_H100_v5"


def test_daemon_preflight_rejects_an_unsupported_shape_without_sdk_call() -> None:
    app = FastAPI()
    compute.register(app, {})
    with (
        patch(
            "general_ludd.routers.compute.build_default_azure_preflight"
        ) as build,
        TestClient(app) as client,
    ):
        response = client.post(
            "/admin/compute/azure/preflight",
            json={
                "gpu_type": "h100",
                "gpu_count": 4,
                "region": "eastus",
            },
        )
    assert response.status_code == 422
    build.assert_not_called()


def test_azure_deploy_refuses_spend_when_read_only_preflight_is_blocked() -> None:
    app = FastAPI()
    manager = AsyncMock()
    app.state._deployment_manager = manager
    compute.register(app, {})
    blocked = replace(
        _ready_result(),
        ready=False,
        blockers=("insufficient family quota",),
    )
    with (
        patch(
            "general_ludd.routers.compute.build_default_azure_preflight"
        ) as build,
        patch("general_ludd.routers.compute.precheck", return_value=([], [])),
        TestClient(app) as client,
    ):
        build.return_value.check.return_value = blocked
        response = client.post(
            "/admin/compute/deploy",
            json={
                "provider": "azure",
                "gpu_type": "h100",
                "gpu_count": 1,
                "model_name": "org/model",
                "region": "eastus",
            },
        )
    assert response.status_code == 409
    manager.deploy.assert_not_awaited()


def test_azure_deploy_registers_ready_endpoint_for_scheduler() -> None:
    app = FastAPI()
    manager = AsyncMock()
    manager.deploy.return_value = ComputeInstance(
        instance_id="gludd-a1b2c3",
        provider=ComputeProvider.AZURE,
        gpu_type=GPUType.H100,
        status="running",
        ip_address="203.0.113.4",
        endpoint_url="http://203.0.113.4:8000/v1",
    )
    app.state._deployment_manager = manager
    compute.register(app, {})
    utilization = MagicMock()
    with (
        patch(
            "general_ludd.routers.compute.build_default_azure_preflight"
        ) as build,
        patch("general_ludd.routers.compute.precheck", return_value=([], [])),
        patch(
            "general_ludd.routers.compute._get_or_create_extended_subsystems",
            return_value={"utilization": utilization},
        ),
        TestClient(app) as client,
    ):
        build.return_value.check.return_value = _ready_result()
        response = client.post(
            "/admin/compute/deploy",
            json={
                "provider": "azure",
                "gpu_type": "h100",
                "gpu_count": 1,
                "model_name": "org/model",
                "region": "eastus",
                "max_concurrent": 8,
            },
        )
    assert response.status_code == 200
    utilization.register_endpoint.assert_called_once_with(
        endpoint_id="gludd-a1b2c3",
        url="http://203.0.113.4:8000/v1",
        model="org/model",
        gpu_type="h100",
        gpu_count=1,
        max_concurrent=8,
    )
