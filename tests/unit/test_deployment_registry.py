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
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from general_ludd.config.binary_paths import BinaryPathResolver, BinaryPaths
from general_ludd.db.models import Base
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

        async def fake_run(args, *, cwd=None, env=None):
            # New API: the per-instance terraform dir is passed via cwd=, not by
            # mutating a shared _active_working_dir attribute.
            captured_dirs.append(cwd)
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

    @pytest.mark.asyncio
    async def test_database_registry_is_shared_across_managers(self, tmp_path):
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'shared.db'}")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        manager_a = DeploymentManager(
            binary_paths=BinaryPathResolver(config=BinaryPaths()),
            working_dir=str(tmp_path / "worker-a"),
            session_factory=sessions,
            worker_id="worker-a",
        )
        manager_b = DeploymentManager(
            binary_paths=BinaryPathResolver(config=BinaryPaths()),
            working_dir=str(tmp_path / "worker-b"),
            session_factory=sessions,
            worker_id="worker-b",
        )
        with patch.object(manager_a, "_run_terraform", new_callable=AsyncMock) as run:
            run.return_value = {
                "stdout": json.dumps({"instance_ip": {"value": "10.0.0.1"}}),
            }
            instance = await manager_a.deploy(_make_config())

        shared = await manager_b.get_deployment_shared(instance.instance_id)
        assert shared is not None
        assert shared.working_dir.startswith(str(tmp_path / "worker-a"))
        assert [record.instance_id for record in await manager_b.list_deployments_shared()] == [
            instance.instance_id
        ]
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_database_destroy_can_be_claimed_by_another_worker(self, tmp_path):
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'destroy.db'}")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        manager_a = DeploymentManager(
            binary_paths=BinaryPathResolver(config=BinaryPaths()),
            working_dir=str(tmp_path / "worker-a"),
            session_factory=sessions,
            worker_id="worker-a",
        )
        manager_b = DeploymentManager(
            binary_paths=BinaryPathResolver(config=BinaryPaths()),
            working_dir=str(tmp_path / "worker-b"),
            session_factory=sessions,
            worker_id="worker-b",
        )
        with patch.object(manager_a, "_run_terraform", new_callable=AsyncMock) as deploy_run:
            deploy_run.return_value = {
                "stdout": json.dumps({"instance_ip": {"value": "10.0.0.2"}}),
            }
            instance = await manager_a.deploy(_make_config())
        with patch.object(manager_b, "_run_terraform", new_callable=AsyncMock) as destroy_run:
            destroy_run.return_value = {"stdout": "", "returncode": 0}
            await manager_b.destroy(instance.instance_id)
            assert destroy_run.await_args.kwargs["cwd"].startswith(str(tmp_path / "worker-a"))
        assert await manager_a.get_deployment_shared(instance.instance_id) is None
        await engine.dispose()
