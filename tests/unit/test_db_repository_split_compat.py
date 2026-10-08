"""Compatibility contract for the split database repository facade."""

from __future__ import annotations

import inspect
import subprocess
import sys

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import general_ludd.db as db_package
import general_ludd.db.repository as facade
from general_ludd.db.models import Base, ModelCallLogModel
from general_ludd.db.repositories import memory, messaging, metrics, operations, projects, shared, todos

_EXTRACTED = {
    "AgentMessageRepository": messaging.AgentMessageRepository,
    "AuditEventRepository": messaging.AuditEventRepository,
    "BenchmarkRepository": metrics.BenchmarkRepository,
    "FeatureRepository": projects.FeatureRepository,
    "HumanTodoRepository": operations.HumanTodoRepository,
    "MemoryRepository": memory.MemoryRepository,
    "ModelPerformanceRepository": metrics.ModelPerformanceRepository,
    "ProjectRelationshipRepository": projects.ProjectRelationshipRepository,
    "ProjectRepository": projects.ProjectRepository,
    "PromptProfileRepository": metrics.PromptProfileRepository,
    "QueueRepository": operations.QueueRepository,
    "RemediationActionRepository": operations.RemediationActionRepository,
    "RoleRunRepository": metrics.RoleRunRepository,
    "SlurmJobRepository": operations.SlurmJobRepository,
    "SpendRepository": metrics.SpendRepository,
    "TaskReturnRepository": todos.TaskReturnRepository,
    "TodoRepository": todos.TodoRepository,
    "VariableNamespaceRepository": projects.VariableNamespaceRepository,
}


@pytest.mark.parametrize(("name", "implementation"), _EXTRACTED.items())
def test_facade_reexports_exact_repository_objects(name: str, implementation: type[object]) -> None:
    """Facade imports preserve identity and constructor signatures."""
    exported = getattr(facade, name)
    assert exported is implementation
    assert inspect.signature(exported) == inspect.signature(implementation)


@pytest.mark.parametrize(
    ("name", "implementation"),
    {
        "AuditEventRepository": messaging.AuditEventRepository,
        "BenchmarkRepository": metrics.BenchmarkRepository,
        "ModelPerformanceRepository": metrics.ModelPerformanceRepository,
        "ProjectRepository": projects.ProjectRepository,
        "PromptProfileRepository": metrics.PromptProfileRepository,
        "QueueRepository": operations.QueueRepository,
        "TaskReturnRepository": todos.TaskReturnRepository,
        "TodoRepository": todos.TodoRepository,
        "VariableNamespaceRepository": projects.VariableNamespaceRepository,
    }.items(),
)
def test_package_reexports_keep_exact_identities(name: str, implementation: type[object]) -> None:
    """Long-standing ``general_ludd.db`` imports remain exact classes."""
    assert getattr(db_package, name) is implementation
    assert getattr(db_package, name) is getattr(facade, name)


def test_shared_facade_symbols_keep_exact_identities() -> None:
    """Public helpers and errors move without wrappers or signature drift."""
    assert facade.ConcurrencyError is shared.ConcurrencyError
    assert facade.InvalidTransitionError is shared.InvalidTransitionError
    assert facade.scoped_to is shared.scoped_to
    assert inspect.signature(facade.scoped_to) == inspect.signature(shared.scoped_to)


@pytest.mark.parametrize(
    "imports",
    [
        *((f"general_ludd.db.repositories.{module}", "general_ludd.db.repository") for module in (
            "memory",
            "messaging",
            "metrics",
            "operations",
            "projects",
            "shared",
            "todos",
        )),
        *(("general_ludd.db.repository", f"general_ludd.db.repositories.{module}") for module in (
            "memory",
            "messaging",
            "metrics",
            "operations",
            "projects",
            "shared",
            "todos",
        )),
    ],
)
def test_cold_import_order_is_cycle_free(imports: tuple[str, str]) -> None:
    """Each implementation module imports before or after the facade."""
    code = (
        "import importlib; "
        f"first=importlib.import_module({imports[0]!r}); "
        f"second=importlib.import_module({imports[1]!r}); "
        "assert first is not None and second is not None"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


async def test_model_performance_write_stays_in_callers_transaction() -> None:
    """Extracted repositories flush but never commit the caller's transaction."""
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        repository = facade.ModelPerformanceRepository(session=session)
        await repository.record_call(
            service="test",
            model_name="rollback-model",
            model_profile_id="test/rollback-model",
            task_type="compatibility",
            success=True,
            cost_usd=0.0,
            duration_ms=1.0,
            session=session,
        )
        assert await session.scalar(select(func.count()).select_from(ModelCallLogModel)) == 1
        await session.rollback()

    async with factory() as verification_session:
        assert await verification_session.scalar(
            select(func.count()).select_from(ModelCallLogModel)
        ) == 0
    await engine.dispose()


async def test_extracted_repository_reads_facade_list_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """The historical facade monkeypatch still bounds extracted repositories."""
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        repository = facade.RoleRunRepository(session)
        for index in range(4):
            await repository.record("project", f"role-{index}")
        monkeypatch.setattr(facade, "_DEFAULT_LIST_LIMIT", 2)
        assert len(await repository.list_all(project_id="project")) == 2

    await engine.dispose()


async def test_spend_list_since_keeps_project_filter() -> None:
    """The extracted metrics query retains its optional tenant predicate."""
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        repository = facade.SpendRepository(session)
        await repository.add(1.0, 1.0, "model", project_id="project-a")
        await repository.add(2.0, 2.0, "model", project_id="project-b")
        rows = await repository.list_since(0.0, project_id="project-a")
        assert [row.project_id for row in rows] == ["project-a"]

    await engine.dispose()


async def test_human_todo_list_all_preserves_every_optional_filter() -> None:
    """The extracted operations repository retains its full filter matrix."""
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        repository = facade.HumanTodoRepository(session)
        expected = await repository.create(
            agent_id="agent-a",
            title="choose",
            body="pick one",
            category="decision",
            priority="high",
        )
        await repository.create(
            agent_id="agent-b",
            title="blocked",
            body="needs access",
            category="blocker",
            priority="low",
        )

        assert len(await repository.list_all()) == 2
        filtered = await repository.list_all(
            status="open",
            category="decision",
            priority="high",
            agent_id="agent-a",
        )
        assert [row.id for row in filtered] == [expected.id]

    await engine.dispose()
