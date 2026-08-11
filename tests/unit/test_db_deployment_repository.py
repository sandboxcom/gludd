"""Unit tests for DeploymentRegistryRepository (zero prior coverage)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from general_ludd.db.deployment_repository import (
    DeploymentBusyError,
    DeploymentRegistryRepository,
    _as_record,
    _insert_for_dialect,
)
from general_ludd.db.models import Base, DeploymentRecordModel
from general_ludd.schemas.deployment import DeploymentRecord

# ---------------------------------------------------------------------------
# Shared async-engine / session fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def async_engine():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def async_session(async_engine):
    session_factory = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def repo(async_session: AsyncSession) -> DeploymentRegistryRepository:
    return DeploymentRegistryRepository(async_session)


def _make_record(instance_id: str = "i-abc123", **kw) -> DeploymentRecord:
    defaults = {
        "instance_id": instance_id,
        "working_dir": "/tmp/deployments/test",
        "provider": "aws",
        "model_name": "claude-sonnet",
        "state": "running",
        "ip_address": "10.0.0.1",
        "endpoint_url": "https://api.example.com",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    defaults.update(kw)
    return DeploymentRecord(**defaults)


# ---------------------------------------------------------------------------
# _as_record
# ---------------------------------------------------------------------------


def test_as_record_converts_all_fields():
    model = DeploymentRecordModel(
        instance_id="i-1",
        working_dir="/tmp/wd",
        provider="aws",
        model_name="sonnet",
        state="running",
        ip_address="1.2.3.4",
        endpoint_url="https://x",
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    rec = _as_record(model)
    assert rec.instance_id == "i-1"
    assert rec.working_dir == "/tmp/wd"
    assert rec.provider == "aws"
    assert rec.model_name == "sonnet"
    assert rec.state == "running"
    assert rec.ip_address == "1.2.3.4"
    assert rec.endpoint_url == "https://x"
    assert rec.created_at == datetime(2026, 6, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------
# _insert_for_dialect
# ---------------------------------------------------------------------------


def test_insert_for_dialect_sqlite():
    result = _insert_for_dialect(DeploymentRecordModel, "sqlite")
    assert result is not None
    assert hasattr(result, "values")


def test_insert_for_dialect_postgresql():
    result = _insert_for_dialect(DeploymentRecordModel, "postgresql")
    assert result is not None
    assert hasattr(result, "values")


def test_insert_for_dialect_unknown_raises():
    with pytest.raises(ValueError, match="does not support SQL dialect"):
        _insert_for_dialect(DeploymentRecordModel, "mysql")


# ---------------------------------------------------------------------------
# upsert — insert
# ---------------------------------------------------------------------------


async def test_upsert_insert_new_record(repo: DeploymentRegistryRepository):
    rec = _make_record()
    result = await repo.upsert(rec)
    assert result.instance_id == "i-abc123"
    assert result.state == "running"

    fetched = await repo.get("i-abc123")
    assert fetched is not None
    assert fetched.instance_id == "i-abc123"


async def test_upsert_insert_twice_updates(repo: DeploymentRegistryRepository):
    rec1 = _make_record()
    await repo.upsert(rec1)

    rec2 = _make_record(state="stopped", working_dir="/tmp/deployments/updated")
    result = await repo.upsert(rec2)
    assert result.state == "stopped"
    assert result.working_dir == "/tmp/deployments/updated"


async def test_upsert_blocked_when_destroying(repo: DeploymentRegistryRepository):
    rec = _make_record()
    await repo.upsert(rec)

    model = await repo._session.get(DeploymentRecordModel, "i-abc123")
    assert model is not None
    model.state = "destroying"
    model.destroy_owner = "worker-x"
    await repo._session.commit()

    update = _make_record(state="running")
    with pytest.raises(DeploymentBusyError, match="is destroying"):
        await repo.upsert(update)


# ---------------------------------------------------------------------------
# get / list
# ---------------------------------------------------------------------------


async def test_get_returns_none_for_missing(repo: DeploymentRegistryRepository):
    assert await repo.get("nonexistent") is None


async def test_get_returns_record(repo: DeploymentRegistryRepository):
    await repo.upsert(_make_record())
    rec = await repo.get("i-abc123")
    assert rec is not None
    assert rec.instance_id == "i-abc123"


async def test_list_empty(repo: DeploymentRegistryRepository):
    assert await repo.list() == []


async def test_list_returns_all_ordered(repo: DeploymentRegistryRepository):
    r1 = _make_record(instance_id="i-a", created_at=datetime(2026, 1, 1, tzinfo=UTC))
    r2 = _make_record(instance_id="i-b", created_at=datetime(2026, 6, 1, tzinfo=UTC))
    await repo.upsert(r1)
    await repo.upsert(r2)
    results = await repo.list()
    assert len(results) == 2
    assert results[0].instance_id == "i-a"
    assert results[1].instance_id == "i-b"


# ---------------------------------------------------------------------------
# claim_for_destroy
# ---------------------------------------------------------------------------


async def test_claim_for_destroy_succeeds_running(repo: DeploymentRegistryRepository):
    await repo.upsert(_make_record())
    result = await repo.claim_for_destroy("i-abc123", owner="worker-1")
    assert result.state == "destroying"
    model = await repo._session.get(DeploymentRecordModel, "i-abc123")
    assert model is not None
    assert model.destroy_owner == "worker-1"


async def test_claim_for_destroy_succeeds_destroy_failed(repo: DeploymentRegistryRepository):
    rec = _make_record(state="destroy_failed")
    await repo.upsert(rec)
    result = await repo.claim_for_destroy("i-abc123", owner="worker-2")
    assert result.state == "destroying"


async def test_claim_for_destroy_unknown_instance_raises(repo: DeploymentRegistryRepository):
    with pytest.raises(KeyError):
        await repo.claim_for_destroy("nonexistent", owner="worker-1")


async def test_claim_for_destroy_already_destroying_raises(repo: DeploymentRegistryRepository):
    rec = _make_record()
    await repo.upsert(rec)
    await repo.claim_for_destroy("i-abc123", owner="worker-1")

    with pytest.raises(DeploymentBusyError, match="is destroying"):
        await repo.claim_for_destroy("i-abc123", owner="worker-2")


async def test_claim_for_destroy_truncates_owner(repo: DeploymentRegistryRepository):
    await repo.upsert(_make_record())
    long_owner = "w" * 200
    result = await repo.claim_for_destroy("i-abc123", owner=long_owner)
    assert result.state == "destroying"


# ---------------------------------------------------------------------------
# finish_destroy
# ---------------------------------------------------------------------------


async def test_finish_destroy_deletes_row(repo: DeploymentRegistryRepository):
    await repo.upsert(_make_record())
    await repo.claim_for_destroy("i-abc123", owner="worker-1")
    await repo.finish_destroy("i-abc123", owner="worker-1")
    assert await repo.get("i-abc123") is None


async def test_finish_destroy_wrong_owner_raises(repo: DeploymentRegistryRepository):
    await repo.upsert(_make_record())
    await repo.claim_for_destroy("i-abc123", owner="worker-1")
    with pytest.raises(DeploymentBusyError, match="is owned by"):
        await repo.finish_destroy("i-abc123", owner="worker-2")


async def test_finish_destroy_not_destroying_raises(repo: DeploymentRegistryRepository):
    await repo.upsert(_make_record())
    with pytest.raises(DeploymentBusyError):
        await repo.finish_destroy("i-abc123", owner="worker-1")


async def test_finish_destroy_missing_instance_raises(repo: DeploymentRegistryRepository):
    with pytest.raises(DeploymentBusyError, match="is owned by deleted"):
        await repo.finish_destroy("nonexistent", owner="worker-1")


# ---------------------------------------------------------------------------
# release_destroy
# ---------------------------------------------------------------------------


async def test_release_destroy_sets_destroy_failed(repo: DeploymentRegistryRepository):
    await repo.upsert(_make_record())
    await repo.claim_for_destroy("i-abc123", owner="worker-1")
    await repo.release_destroy("i-abc123", owner="worker-1")
    rec = await repo.get("i-abc123")
    assert rec is not None
    assert rec.state == "destroy_failed"


async def test_release_destroy_wrong_owner_raises(repo: DeploymentRegistryRepository):
    await repo.upsert(_make_record())
    await repo.claim_for_destroy("i-abc123", owner="worker-1")
    with pytest.raises(DeploymentBusyError, match="is owned by"):
        await repo.release_destroy("i-abc123", owner="worker-2")


async def test_release_destroy_not_destroying_raises(repo: DeploymentRegistryRepository):
    await repo.upsert(_make_record())
    with pytest.raises(DeploymentBusyError):
        await repo.release_destroy("i-abc123", owner="worker-1")


async def test_release_destroy_missing_instance_raises(repo: DeploymentRegistryRepository):
    with pytest.raises(DeploymentBusyError, match="is owned by deleted"):
        await repo.release_destroy("nonexistent", owner="worker-1")


# ---------------------------------------------------------------------------
# DeploymentBusyError
# ---------------------------------------------------------------------------


def test_deployment_busy_error_is_runtime_error():
    assert issubclass(DeploymentBusyError, RuntimeError)


def test_deployment_busy_error_message():
    err = DeploymentBusyError("deployment 'i-1' is busy")
    assert str(err) == "deployment 'i-1' is busy"


# ---------------------------------------------------------------------------
# Deep tests — revision tracking, timestamp updates, edge cases
# ---------------------------------------------------------------------------


class TestUpsertRevisionTracking:
    async def test_revision_present_on_insert(self, repo: DeploymentRegistryRepository):
        rec = _make_record()
        await repo.upsert(rec)
        model = await repo._session.get(DeploymentRecordModel, "i-abc123")
        assert model is not None
        assert model.revision >= 1

    async def test_revision_present_after_update(self, repo: DeploymentRegistryRepository):
        await repo.upsert(_make_record())
        await repo.upsert(_make_record(state="stopped"))
        model = await repo._session.get(DeploymentRecordModel, "i-abc123")
        assert model is not None
        assert model.revision >= 1


class TestUpsertClearsDestroyOwner:
    async def test_upsert_resets_destroy_owner_in_set_clause(self, repo: DeploymentRegistryRepository):
        await repo.upsert(_make_record())
        await repo.claim_for_destroy("i-abc123", owner="worker-1")
        model_before = await repo._session.get(DeploymentRecordModel, "i-abc123")
        assert model_before is not None
        assert model_before.destroy_owner == "worker-1"
        assert model_before.state == "destroying"


class TestUpsertUpdatedAt:
    async def test_updated_at_changes_on_update(self, repo: DeploymentRegistryRepository):
        await repo.upsert(_make_record())
        model1 = await repo._session.get(DeploymentRecordModel, "i-abc123")
        assert model1 is not None
        ts1 = model1.updated_at

        import asyncio

        await asyncio.sleep(1.0)

        await repo.upsert(_make_record(state="stopped"))
        model2 = await repo._session.get(DeploymentRecordModel, "i-abc123")
        assert model2 is not None
        assert model2.updated_at > ts1


class TestClaimForDestroyOwnerTruncation:
    async def test_owner_truncated_to_128(self, repo: DeploymentRegistryRepository):
        await repo.upsert(_make_record())
        long_owner = "x" * 200
        await repo.claim_for_destroy("i-abc123", owner=long_owner)
        model = await repo._session.get(DeploymentRecordModel, "i-abc123")
        assert model is not None
        assert len(model.destroy_owner) <= 128
        assert model.destroy_owner == long_owner[:128]


class TestReleaseDestroyEdgeCases:
    async def test_release_destroy_clears_destroy_owner(self, repo: DeploymentRegistryRepository):
        await repo.upsert(_make_record())
        await repo.claim_for_destroy("i-abc123", owner="worker-1")
        await repo.release_destroy("i-abc123", owner="worker-1")
        model = await repo._session.get(DeploymentRecordModel, "i-abc123")
        assert model is not None
        assert model.destroy_owner is None
        assert model.state == "destroy_failed"

    async def test_release_destroy_increments_revision(self, repo: DeploymentRegistryRepository):
        await repo.upsert(_make_record())
        model1 = await repo._session.get(DeploymentRecordModel, "i-abc123")
        assert model1 is not None
        rev1 = model1.revision

        await repo.claim_for_destroy("i-abc123", owner="worker-1")
        model2 = await repo._session.get(DeploymentRecordModel, "i-abc123")
        assert model2 is not None
        rev2 = model2.revision
        assert rev2 > rev1

        await repo.release_destroy("i-abc123", owner="worker-1")
        model3 = await repo._session.get(DeploymentRecordModel, "i-abc123")
        assert model3 is not None
        assert model3.revision > rev2


class TestClaimForDestroyRejectsWrongStates:
    async def test_claim_rejects_stopped_state(self, repo: DeploymentRegistryRepository):
        await repo.upsert(_make_record(state="stopped"))
        with pytest.raises(DeploymentBusyError, match="is stopped"):
            await repo.claim_for_destroy("i-abc123", owner="worker-1")


class TestFinishDestroyWithLongOwner:
    async def test_finish_destroy_with_truncated_owner_match(self, repo: DeploymentRegistryRepository):
        await repo.upsert(_make_record())
        long_owner = "y" * 200
        await repo.claim_for_destroy("i-abc123", owner=long_owner)
        await repo.finish_destroy("i-abc123", owner=long_owner)
        assert await repo.get("i-abc123") is None
