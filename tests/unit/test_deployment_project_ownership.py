"""Durable project ownership tests for the shared deployment registry."""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from general_ludd.db.deployment_repository import DeploymentRegistryRepository
from general_ludd.db.migrations import get_alembic_config
from general_ludd.db.models import Base
from general_ludd.infra.deployment import DeploymentManager
from general_ludd.schemas.deployment import DeploymentRecord


def _record(
    project_id: str,
    *,
    provider: str = "azure",
    instance_id: str = "shared-gpu",
) -> DeploymentRecord:
    return DeploymentRecord(
        project_id=project_id,
        provider=provider,
        instance_id=instance_id,
        working_dir=f"/tmp/{project_id}/{provider}/{instance_id}",
        model_name="owned-model",
    )


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine, expire_on_commit=False) as value:
        yield value
    await engine.dispose()


@pytest.mark.asyncio
async def test_composite_identity_preserves_same_cloud_id_across_projects(
    session: AsyncSession,
) -> None:
    repository = DeploymentRegistryRepository(session)
    await repository.upsert(_record("project-a"))
    await repository.upsert(_record("project-b"))
    await session.commit()

    project_a = await repository.get(
        "shared-gpu",
        project_id="project-a",
        provider="azure",
    )
    project_b = await repository.get(
        "shared-gpu",
        project_id="project-b",
        provider="azure",
    )

    assert project_a is not None and project_a.project_id == "project-a"
    assert project_b is not None and project_b.project_id == "project-b"


@pytest.mark.asyncio
async def test_unscoped_lookup_rejects_ambiguous_instance_id(
    session: AsyncSession,
) -> None:
    repository = DeploymentRegistryRepository(session)
    await repository.upsert(_record("project-a"))
    await repository.upsert(_record("project-b"))

    with pytest.raises(RuntimeError, match="ambiguous deployment identity"):
        await repository.get("shared-gpu")


@pytest.mark.asyncio
async def test_project_list_never_leaks_another_projects_deployments(
    session: AsyncSession,
) -> None:
    repository = DeploymentRegistryRepository(session)
    await repository.upsert(_record("project-a", instance_id="gpu-a"))
    await repository.upsert(_record("project-b", instance_id="gpu-b"))

    records = await repository.list(
        project_id="project-a",
        provider="azure",
    )

    assert [(record.project_id, record.instance_id) for record in records] == [
        ("project-a", "gpu-a")
    ]


@pytest.mark.asyncio
async def test_scoped_destroy_cannot_delete_another_projects_record(
    session: AsyncSession,
) -> None:
    repository = DeploymentRegistryRepository(session)
    await repository.upsert(_record("project-a"))
    await repository.upsert(_record("project-b"))
    claimed = await repository.claim_for_destroy(
        "shared-gpu",
        owner="worker-a",
        project_id="project-a",
        provider="azure",
    )
    assert claimed.project_id == "project-a"

    await repository.finish_destroy(
        "shared-gpu",
        owner="worker-a",
        project_id="project-a",
        provider="azure",
    )

    assert (
        await repository.get(
            "shared-gpu",
            project_id="project-a",
            provider="azure",
        )
        is None
    )
    assert (
        await repository.get(
            "shared-gpu",
            project_id="project-b",
            provider="azure",
        )
        is not None
    )


def test_migration_047_adds_composite_deployment_identity() -> None:
    migration = Path(
        "alembic/versions/047_add_deployment_project_ownership.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "047"' in migration
    assert 'down_revision: str | None = "046"' in migration
    assert '"project_id", "provider", "instance_id"' in migration
    assert "ix_deployment_records_project_id" in migration


def test_migration_backfills_legacy_and_refuses_lossy_downgrade(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'ownership-migration.db'}"
    config = get_alembic_config(database_url)
    command.upgrade(config, "046")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO deployment_records "
                "(instance_id, working_dir, provider, model_name, state, "
                "destroy_owner, revision, created_at, updated_at) VALUES "
                "('shared-gpu', '/tmp/legacy', 'azure', 'model', 'running', "
                "NULL, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
    engine.dispose()

    command.upgrade(config, "047")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        assert connection.execute(
            text(
                "SELECT project_id FROM deployment_records "
                "WHERE instance_id = 'shared-gpu'"
            )
        ).scalar_one() == "default"
        connection.execute(
            text(
                "INSERT INTO deployment_records "
                "(project_id, instance_id, working_dir, provider, model_name, "
                "state, destroy_owner, revision, created_at, updated_at) VALUES "
                "('project-b', 'shared-gpu', '/tmp/project-b', 'azure', "
                "'model', 'running', NULL, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
    assert inspect(engine).get_pk_constraint("deployment_records")[
        "constrained_columns"
    ] == ["project_id", "provider", "instance_id"]
    engine.dispose()

    with pytest.raises(RuntimeError, match="cannot downgrade deployment ownership"):
        command.downgrade(config, "046")


@pytest.mark.asyncio
async def test_local_registry_preserves_same_instance_id_across_providers(
    tmp_path: Path,
) -> None:
    manager = DeploymentManager(
        working_dir=str(tmp_path),
        project_id="project-a",
    )
    await manager._persist_record(_record("project-a", provider="azure"))
    await manager._persist_record(_record("project-a", provider="aws"))

    assert {
        (record.provider, record.instance_id)
        for record in manager.list_deployments()
    } == {("azure", "shared-gpu"), ("aws", "shared-gpu")}
    with pytest.raises(ValueError, match="ambiguous deployment identity"):
        manager.get_deployment("shared-gpu")
    assert manager.get_deployment("shared-gpu", provider="aws") is not None

    restarted = DeploymentManager(
        working_dir=str(tmp_path),
        project_id="project-a",
    )
    assert {
        (record.provider, record.instance_id)
        for record in restarted.list_deployments()
    } == {("azure", "shared-gpu"), ("aws", "shared-gpu")}
