"""W2.3 (C5/M2): deploy-before-destroy registry.

The money-leak bug: destroy() ran terraform destroy with no record of WHAT was
deployed or WHERE, and would happily destroy on an instance_id it never saw.
The fix: a per-instance_id registry persisted at deploy time keyed by
instance_id -> (working_dir, state). destroy() refuses an unknown instance_id and
runs in that deployment's own working dir. /api/deployments exposes the registry.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from general_ludd.config.binary_paths import BinaryPathResolver, BinaryPaths
from general_ludd.infra.compute import ComputeConfig, ComputeProvider, GPUType
from general_ludd.infra.deployment import DeploymentManager
from general_ludd.schemas.deployment import DeploymentRecord


def _make_config(model_name: str = "test-model") -> ComputeConfig:
    return ComputeConfig(
        provider=ComputeProvider.AWS,
        gpu_type=GPUType.T4,
        model_name=model_name,
        region="us-east-1",
    )


def _mgr(tmp_path) -> DeploymentManager:
    return DeploymentManager(
        binary_paths=BinaryPathResolver(config=BinaryPaths()),
        working_dir=str(tmp_path),
    )


class TestDeployRegistersInstance:
    @pytest.mark.asyncio
    async def test_deploy_records_instance_in_registry(self, tmp_path):
        mgr = _mgr(tmp_path)
        with patch.object(mgr, "_run_terraform", new_callable=AsyncMock) as run:
            run.return_value = {
                "stdout": json.dumps({"instance_ip": {"value": "1.2.3.4"}}),
            }
            instance = await mgr.deploy(_make_config())

        record = mgr.get_deployment(instance.instance_id)
        assert record is not None
        assert isinstance(record, DeploymentRecord)
        assert record.instance_id == instance.instance_id
        assert record.working_dir  # the per-instance terraform dir
        assert record.state == "running"

    @pytest.mark.asyncio
    async def test_deploy_uses_per_instance_working_dir(self, tmp_path):
        mgr = _mgr(tmp_path)
        with patch.object(mgr, "_run_terraform", new_callable=AsyncMock) as run:
            run.return_value = {"stdout": json.dumps({"instance_ip": {"value": "9.9.9.9"}})}
            instance = await mgr.deploy(_make_config())
        record = mgr.get_deployment(instance.instance_id)
        # the instance dir is nested under the manager's base working dir
        assert str(tmp_path) in record.working_dir


class TestDestroyRefusesUnknown:
    @pytest.mark.asyncio
    async def test_destroy_unknown_instance_raises(self, tmp_path):
        mgr = _mgr(tmp_path)
        with pytest.raises(ValueError, match=r"unknown|not found|no deployment"):
            await mgr.destroy("i-never-deployed")

    @pytest.mark.asyncio
    async def test_destroy_after_deploy_runs_in_recorded_dir(self, tmp_path):
        mgr = _mgr(tmp_path)
        captured_dirs: list[str] = []

        async def fake_run(args):
            captured_dirs.append(mgr._active_working_dir)
            return {"stdout": json.dumps({"instance_ip": {"value": "5.5.5.5"}})}

        with patch.object(mgr, "_run_terraform", side_effect=fake_run):
            instance = await mgr.deploy(_make_config())
            captured_dirs.clear()
            await mgr.destroy(instance.instance_id)

        record_dir_seen = captured_dirs[-1]
        assert instance.instance_id  # sanity
        # destroy ran against the per-instance dir that deploy created
        assert record_dir_seen.startswith(str(tmp_path))

    @pytest.mark.asyncio
    async def test_destroy_removes_from_registry(self, tmp_path):
        mgr = _mgr(tmp_path)
        with patch.object(mgr, "_run_terraform", new_callable=AsyncMock) as run:
            run.return_value = {"stdout": json.dumps({"instance_ip": {"value": "1.1.1.1"}})}
            instance = await mgr.deploy(_make_config())
            await mgr.destroy(instance.instance_id)
        assert mgr.get_deployment(instance.instance_id) is None


class TestRegistryPersistence:
    @pytest.mark.asyncio
    async def test_registry_survives_new_manager(self, tmp_path):
        mgr = _mgr(tmp_path)
        with patch.object(mgr, "_run_terraform", new_callable=AsyncMock) as run:
            run.return_value = {"stdout": json.dumps({"instance_ip": {"value": "2.2.2.2"}})}
            instance = await mgr.deploy(_make_config())

        # Restart: a fresh manager over the same base dir reloads the registry.
        mgr2 = _mgr(tmp_path)
        record = mgr2.get_deployment(instance.instance_id)
        assert record is not None
        assert record.instance_id == instance.instance_id

    @pytest.mark.asyncio
    async def test_list_deployments(self, tmp_path):
        mgr = _mgr(tmp_path)
        with patch.object(mgr, "_run_terraform", new_callable=AsyncMock) as run:
            run.return_value = {"stdout": json.dumps({"instance_ip": {"value": "3.3.3.3"}})}
            inst = await mgr.deploy(_make_config())
        listed = mgr.list_deployments()
        assert any(r.instance_id == inst.instance_id for r in listed)
