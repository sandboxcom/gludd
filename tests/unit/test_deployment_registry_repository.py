"""Shared deployment registry and destroy-fencing tests."""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from general_ludd.db.deployment_repository import (
    DeploymentBusyError,
    DeploymentRegistryRepository,
)
from general_ludd.db.models import Base
from general_ludd.schemas.deployment import DeploymentRecord


def _record(instance_id: str, working_dir: str) -> DeploymentRecord:
    return DeploymentRecord(
        instance_id=instance_id,
        working_dir=working_dir,
        provider="azure",
        model_name="fps-model",
        endpoint_url=f"https://{instance_id}.example.test",
    )


@pytest_asyncio.fixture
async def sessions(tmp_path) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'registry.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_independent_workers_do_not_lose_deployment_writes(sessions) -> None:
    async def persist(record: DeploymentRecord) -> None:
        async with sessions() as session:
            await DeploymentRegistryRepository(session).upsert(record)
            await session.commit()

    await asyncio.gather(
        persist(_record("gpu-worker-a", "/tmp/gludd-a")),
        persist(_record("gpu-worker-b", "/tmp/gludd-b")),
    )

    async with sessions() as session:
        records = await DeploymentRegistryRepository(session).list()
    assert {record.instance_id for record in records} == {
        "gpu-worker-a",
        "gpu-worker-b",
    }


@pytest.mark.asyncio
async def test_destroy_claim_is_atomic_and_owner_fenced(sessions) -> None:
    async with sessions() as session:
        repository = DeploymentRegistryRepository(session)
        await repository.upsert(_record("gpu-shared", "/tmp/gludd-shared"))
        await session.commit()

    async with sessions() as worker_a_session, sessions() as worker_b_session:
        worker_a = DeploymentRegistryRepository(worker_a_session)
        worker_b = DeploymentRegistryRepository(worker_b_session)
        claimed = await worker_a.claim_for_destroy("gpu-shared", owner="worker-a")
        await worker_a_session.commit()
        assert claimed.instance_id == "gpu-shared"
        with pytest.raises(DeploymentBusyError, match="worker-a"):
            await worker_b.claim_for_destroy("gpu-shared", owner="worker-b")

    async with sessions() as session:
        repository = DeploymentRegistryRepository(session)
        with pytest.raises(DeploymentBusyError, match="worker-a"):
            await repository.finish_destroy("gpu-shared", owner="worker-b")
        await session.rollback()
        await repository.finish_destroy("gpu-shared", owner="worker-a")
        await session.commit()

    async with sessions() as session:
        assert await DeploymentRegistryRepository(session).get("gpu-shared") is None


@pytest.mark.asyncio
async def test_failed_destroy_releases_claim_for_retry(sessions) -> None:
    async with sessions() as session:
        repository = DeploymentRegistryRepository(session)
        await repository.upsert(_record("gpu-retry", "/tmp/gludd-retry"))
        await repository.claim_for_destroy("gpu-retry", owner="worker-a")
        await repository.release_destroy("gpu-retry", owner="worker-a")
        await session.commit()

    async with sessions() as session:
        claimed = await DeploymentRegistryRepository(session).claim_for_destroy(
            "gpu-retry", owner="worker-b"
        )
        assert claimed.state == "destroying"


@pytest.mark.asyncio
async def test_upsert_cannot_clobber_an_active_destroy_claim(sessions) -> None:
    original = _record("gpu-destroying", "/tmp/gludd-original")
    async with sessions() as session:
        repository = DeploymentRegistryRepository(session)
        await repository.upsert(original)
        await repository.claim_for_destroy(original.instance_id, owner="worker-a")
        await session.commit()

    async with sessions() as session:
        with pytest.raises(DeploymentBusyError, match="destroying"):
            await DeploymentRegistryRepository(session).upsert(
                _record(original.instance_id, "/tmp/gludd-clobber")
            )
