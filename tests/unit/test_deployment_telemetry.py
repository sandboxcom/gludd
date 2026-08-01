"""Telemetry and cancellation safety for Terraform deployments."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from general_ludd.events import EventBus
from general_ludd.infra import deployment as deployment_module
from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType
from general_ludd.infra.deployment import DeploymentManager
from general_ludd.schemas.deployment import DeploymentRecord


def _azure_container_app_config() -> ComputeConfig:
    return ComputeConfig(
        provider=ComputeProvider.AZURE,
        gpu_type=GPUType.A100_80,
        model_name="test-model",
        region="eastus",
        deploy_type="containerapp",
        spot=False,
    )


def _event_names(bus: EventBus) -> list[str]:
    return [str(event.payload["name"]) for event in bus.get_history()]


@pytest.mark.asyncio
async def test_deploy_attributes_elapsed_cost_and_publishes_lifecycle(tmp_path) -> None:
    bus = EventBus(history_size=20)
    manager = DeploymentManager(working_dir=str(tmp_path), event_bus=bus)

    async def fake_terraform(
        args: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        del cwd, env
        if args[0] == "output":
            return {
                "stdout": json.dumps(
                    {
                        "instance_ip": {"value": "azure-instance-1"},
                        "endpoint_url": {"value": "https://example.invalid/v1"},
                    }
                ),
                "stderr": "",
                "returncode": 0,
            }
        return {"stdout": "", "stderr": "", "returncode": 0}

    with (
        patch.object(manager, "_run_terraform", side_effect=fake_terraform),
        patch(
            "general_ludd.infra.deployment.time.sleep",
            side_effect=AssertionError("deploy must not block for FQDN propagation"),
        ),
        patch("general_ludd.infra.deployment.time.monotonic", side_effect=[100.0, 220.0]),
    ):
        instance = await manager.deploy(_azure_container_app_config())

    assert instance.cost_incurred == pytest.approx(0.05 * 120.0 / 3600.0)
    assert _event_names(bus) == ["terraform_deploy_started", "terraform_deploy_completed"]
    completed = bus.get_history()[-1]
    assert completed.payload["instance_id"] == "azure-instance-1"
    assert completed.payload["cost_incurred_usd"] == pytest.approx(instance.cost_incurred)
    deployment_module._DEPLOYED_INSTANCES.pop("azure-instance-1", None)


class _LineStream:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = iter(lines)

    async def readline(self) -> bytes:
        return next(self._lines, b"")


class _TerraformProcess:
    def __init__(self, bus: EventBus) -> None:
        self.stdout = _LineStream([b"token=do-not-persist Apply complete\n"])
        self.returncode = 0
        self._bus = bus
        self.output_seen_before_wait = False

    async def wait(self) -> int:
        self.output_seen_before_wait = "terraform_output" in _event_names(self._bus)
        return self.returncode


@pytest.mark.asyncio
async def test_run_terraform_streams_sanitized_output_before_process_exit(tmp_path) -> None:
    bus = EventBus(history_size=20)
    manager = DeploymentManager(working_dir=str(tmp_path), event_bus=bus)
    process = _TerraformProcess(bus)

    with patch(
        "general_ludd.infra.deployment.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=process),
    ):
        result = await manager._run_terraform(["apply"], cwd=str(tmp_path))

    assert result["returncode"] == 0
    assert process.output_seen_before_wait is True
    assert _event_names(bus) == [
        "terraform_command_started",
        "terraform_output",
        "terraform_command_completed",
    ]
    output_event = bus.get_history()[1]
    assert "do-not-persist" not in output_event.payload["message"]
    assert "REDACTED" in output_event.payload["message"]


@pytest.mark.asyncio
async def test_destroy_finishes_cleanup_when_caller_is_cancelled(tmp_path) -> None:
    bus = EventBus(history_size=20)
    manager = DeploymentManager(working_dir=str(tmp_path), event_bus=bus)
    manager._last_config = _azure_container_app_config()
    manager._registry["azure-instance-1"] = DeploymentRecord(
        instance_id="azure-instance-1",
        working_dir=str(tmp_path),
        provider="azure",
        model_name="test-model",
        state="running",
    )

    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def delayed_destroy(
        args: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        del args, cwd, env
        started.set()
        await release.wait()
        finished.set()
        return {"stdout": "", "stderr": "", "returncode": 0}

    with patch.object(manager, "_run_terraform", side_effect=delayed_destroy):
        destroy_task = asyncio.create_task(manager.destroy("azure-instance-1"))
        await started.wait()
        destroy_task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await destroy_task

    assert finished.is_set()
    assert manager.get_deployment("azure-instance-1") is None
    assert _event_names(bus) == ["terraform_destroy_started", "terraform_destroy_completed"]


@pytest.mark.asyncio
async def test_process_cleanup_can_destroy_while_an_event_loop_is_running(tmp_path) -> None:
    (tmp_path / "terraform.tfstate").write_text("{}")
    deployment_module._DEPLOYED_INSTANCES["pending-azure"] = str(tmp_path)
    try:
        with (
            patch.object(deployment_module, "_LIFECYCLE_IMPORTED", False),
            patch("general_ludd.infra.deployment.subprocess.run") as run,
        ):
            run.return_value.returncode = 0
            deployment_module._cleanup_orphaned_instances()

        run.assert_called_once()
        assert "destroy" in run.call_args.args[0]
        assert "pending-azure" not in deployment_module._DEPLOYED_INSTANCES
    finally:
        deployment_module._DEPLOYED_INSTANCES.pop("pending-azure", None)
