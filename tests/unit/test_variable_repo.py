"""Tests for variable namespace repository."""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from general_ludd.db.models import Base
from general_ludd.db.repository import VariableNamespaceRepository
from general_ludd.db.session import run_wal_pragmas


class TestVariableNamespaceRepository:
    @pytest_asyncio.fixture
    async def session(self, tmp_path: Path) -> AsyncIterator[AsyncSession]:
        db = tmp_path / "test.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
        run_wal_pragmas(engine)

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            yield s
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_create_namespace(self, session: AsyncSession) -> None:
        repo = VariableNamespaceRepository(session)
        ns = await repo.create_namespace("shared")
        assert ns.id is not None
        assert ns.namespace == "shared"

    @pytest.mark.asyncio
    async def test_set_var_creates_new(self, session: AsyncSession) -> None:
        repo = VariableNamespaceRepository(session)
        var = await repo.set_var("build", "max_retries", "3")
        assert var.key == "max_retries"
        assert var.value == "3"

    @pytest.mark.asyncio
    async def test_set_var_updates_existing(self, session: AsyncSession) -> None:
        repo = VariableNamespaceRepository(session)
        await repo.set_var("build", "timeout", "30")
        updated = await repo.set_var("build", "timeout", "60")
        assert updated.value == "60"

    @pytest.mark.asyncio
    async def test_load_vars_empty(self, session: AsyncSession) -> None:
        repo = VariableNamespaceRepository(session)
        merged = await repo.load_vars_for_project(None)
        assert merged == {}

    @pytest.mark.asyncio
    async def test_load_vars_returns_all_keys(self, session: AsyncSession) -> None:
        repo = VariableNamespaceRepository(session)
        await repo.set_var("env", "PYTHON_VERSION", "3.14")
        await repo.set_var("env", "UV_CACHE_DIR", "/tmp/uv")
        merged = await repo.load_vars_for_project(None)
        assert merged["PYTHON_VERSION"] == "3.14"
        assert merged["UV_CACHE_DIR"] == "/tmp/uv"

    @pytest.mark.asyncio
    async def test_load_vars_scoped_to_project(self, session: AsyncSession) -> None:
        from general_ludd.db.models import ProjectModel

        project = ProjectModel(project_id="proj-v", name="Var Test")
        session.add(project)
        await session.flush()

        repo = VariableNamespaceRepository(session)
        await repo.set_var("global", "KEY", "global_val")
        await repo.set_var("build", "KEY", "proj_val", project_id="proj-v")

        global_vars = await repo.load_vars_for_project(None)
        assert global_vars["KEY"] == "global_val"
        proj_vars = await repo.load_vars_for_project("proj-v")
        assert proj_vars["KEY"] == "proj_val"

    @pytest.mark.asyncio
    async def test_project_load_quarantines_legacy_global_tool_results(
        self, session: AsyncSession
    ) -> None:
        """Project reads merge global config without leaking global tool output."""
        from general_ludd.db.models import ProjectModel

        session.add_all(
            [
                ProjectModel(project_id="proj-a", name="Project A"),
                ProjectModel(project_id="proj-b", name="Project B"),
            ]
        )
        await session.flush()
        repo = VariableNamespaceRepository(session)
        await repo.set_var("config", "SHARED_SETTING", "enabled")
        await repo.set_var("tool_results", "tool_result:legacy", "legacy-secret")
        await repo.set_var(
            "tool_results",
            "tool_result:project-a",
            "a-secret",
            project_id="proj-a",
        )
        await repo.set_var(
            "tool_results",
            "tool_result:project-b",
            "b-secret",
            project_id="proj-b",
        )

        project_a = await repo.load_vars_for_project("proj-a")
        project_b = await repo.load_vars_for_project("proj-b")

        assert project_a == {
            "SHARED_SETTING": "enabled",
            "tool_result:project-a": "a-secret",
        }
        assert project_b == {
            "SHARED_SETTING": "enabled",
            "tool_result:project-b": "b-secret",
        }
