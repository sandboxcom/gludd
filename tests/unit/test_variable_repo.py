"""Tests for variable namespace repository."""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from general_ludd.db.models import Base
from general_ludd.db.repository import VariableNamespaceRepository
from general_ludd.db.session import run_wal_pragmas


class TestVariableNamespaceRepository:
    @pytest_asyncio.fixture
    async def session(self, tmp_path):
        db = tmp_path / "test.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
        run_wal_pragmas(engine)

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            yield s
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_create_namespace(self, session: AsyncSession):
        repo = VariableNamespaceRepository(session)
        ns = await repo.create_namespace("shared")
        assert ns.id is not None
        assert ns.namespace == "shared"

    @pytest.mark.asyncio
    async def test_set_var_creates_new(self, session: AsyncSession):
        repo = VariableNamespaceRepository(session)
        var = await repo.set_var("build", "max_retries", "3")
        assert var.key == "max_retries"
        assert var.value == "3"

    @pytest.mark.asyncio
    async def test_set_var_updates_existing(self, session: AsyncSession):
        repo = VariableNamespaceRepository(session)
        await repo.set_var("build", "timeout", "30")
        updated = await repo.set_var("build", "timeout", "60")
        assert updated.value == "60"

    @pytest.mark.asyncio
    async def test_load_vars_empty(self, session: AsyncSession):
        repo = VariableNamespaceRepository(session)
        merged = await repo.load_vars_for_project(None)
        assert merged == {}

    @pytest.mark.asyncio
    async def test_load_vars_returns_all_keys(self, session: AsyncSession):
        repo = VariableNamespaceRepository(session)
        await repo.set_var("env", "PYTHON_VERSION", "3.14")
        await repo.set_var("env", "UV_CACHE_DIR", "/tmp/uv")
        merged = await repo.load_vars_for_project(None)
        assert merged["PYTHON_VERSION"] == "3.14"
        assert merged["UV_CACHE_DIR"] == "/tmp/uv"

    @pytest.mark.asyncio
    async def test_load_vars_scoped_to_project(self, session: AsyncSession):
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
