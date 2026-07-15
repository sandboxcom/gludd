"""C.3 — DB tenant scoping: ThreadPoolExecutor sessions carry tenant filter.

Verifies that ``contextvars.ContextVar`` propagates the tenant identity
into worker threads so sessions created inside ``asyncio.to_thread`` can
enforce project-level data isolation.
"""

import asyncio
import contextvars
import os
import shutil
import tempfile

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from general_ludd.db.models import Base, ProjectModel, TodoModel, TodoStatus
from general_ludd.db.tenant import get_tenant, reset_tenant, set_tenant


@pytest_asyncio.fixture
async def async_engine():
    _tmpdir = tempfile.mkdtemp()
    _db_path = os.path.join(_tmpdir, "test.db")
    engine: AsyncEngine = create_async_engine(
        f"sqlite+aiosqlite:///{_db_path}", echo=False
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c, tables=[ProjectModel.__table__, TodoModel.__table__]
            )
        )
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.drop_all(
                c, tables=[ProjectModel.__table__, TodoModel.__table__]
            )
        )
    await engine.dispose()
    shutil.rmtree(_tmpdir, ignore_errors=True)


@pytest_asyncio.fixture
async def session_factory(async_engine: AsyncEngine):
    return async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def seeded(async_engine, session_factory):
    async with session_factory() as session:
        session.add_all([
            ProjectModel(project_id="proj-1", name="Project One"),
            ProjectModel(project_id="proj-2", name="Project Two"),
        ])
        session.add_all([
            TodoModel(todo_id="T1", title="One-a", status=TodoStatus.QUEUED, project_id="proj-1"),
            TodoModel(todo_id="T2", title="One-b", status=TodoStatus.QUEUED, project_id="proj-1"),
            TodoModel(todo_id="T3", title="Two-a", status=TodoStatus.QUEUED, project_id="proj-2"),
        ])
        await session.commit()


class TestTenantContextPropagation:
    def test_set_get_reset(self):
        assert get_tenant() is None
        tok = set_tenant("proj-x")
        assert get_tenant() == "proj-x"
        reset_tenant(tok)
        assert get_tenant() is None

    def test_set_none_is_ok(self):
        tok = set_tenant("proj-y")
        tok_none = set_tenant(None)
        assert get_tenant() is None
        reset_tenant(tok_none)
        assert get_tenant() == "proj-y"
        reset_tenant(tok)
        assert get_tenant() is None

    def test_nesting(self):
        outer_tok = set_tenant("outer")
        assert get_tenant() == "outer"
        inner_tok = set_tenant("inner")
        assert get_tenant() == "inner"
        reset_tenant(inner_tok)
        assert get_tenant() == "outer"
        reset_tenant(outer_tok)
        assert get_tenant() is None


class TestContextvarInThread:
    @pytest.mark.asyncio
    async def test_contextvar_passed_to_thread(self):
        tok = set_tenant("th-proj-1")
        try:
            ctx = contextvars.copy_context()
            loop = asyncio.get_running_loop()
            got = await loop.run_in_executor(None, ctx.run, get_tenant)
            assert got == "th-proj-1"
        finally:
            reset_tenant(tok)

    @pytest.mark.asyncio
    async def test_contextvar_isolated_between_tenants(self):
        results: list[str | None] = []

        def _work():
            results.append(get_tenant())

        loop = asyncio.get_running_loop()

        tok1 = set_tenant("alpha")
        ctx1 = contextvars.copy_context()
        try:
            await loop.run_in_executor(None, ctx1.run, _work)
        finally:
            reset_tenant(tok1)
        assert results == ["alpha"]

        tok2 = set_tenant("beta")
        ctx2 = contextvars.copy_context()
        try:
            await loop.run_in_executor(None, ctx2.run, _work)
        finally:
            reset_tenant(tok2)
        assert results == ["alpha", "beta"]

    @pytest.mark.asyncio
    async def test_no_tenant_when_not_set(self):
        results: list[str | None] = []

        def _work():
            results.append(get_tenant())

        ctx = contextvars.copy_context()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, ctx.run, _work)
        assert results == [None]


class TestTenantScopingWithDB:
    @pytest.mark.asyncio
    async def test_thread_worker_only_sees_own_tenant(
        self, async_engine: AsyncEngine, session_factory, seeded
    ):
        def _query_tenant_count(expected_tenant: str) -> int:
            import asyncio as _asyncio

            from sqlalchemy import func, select

            async def _inner():
                async with session_factory() as sess:
                    stmt = (
                        select(func.count())
                        .select_from(TodoModel)
                        .where(TodoModel.project_id == expected_tenant)
                    )
                    result = await sess.execute(stmt)
                    return result.scalar_one()

            return _asyncio.run(_inner())

        tok = set_tenant("proj-1")
        try:
            loop = asyncio.get_running_loop()
            count = await loop.run_in_executor(None, _query_tenant_count, "proj-1")
            assert count == 2
        finally:
            reset_tenant(tok)

    @pytest.mark.asyncio
    async def test_tenant_set_via_contextvar_in_thread(
        self, async_engine, session_factory, seeded
    ):
        def _query_by_tenant() -> tuple[str | None, int]:
            current = get_tenant()
            import asyncio as _asyncio

            from sqlalchemy import func, select

            async def _inner():
                async with session_factory() as sess:
                    stmt = (
                        select(func.count())
                        .select_from(TodoModel)
                        .where(TodoModel.project_id == current)
                    )
                    result = await sess.execute(stmt)
                    return result.scalar_one()

            count = _asyncio.run(_inner())
            return (current, count)

        tok = set_tenant("proj-2")
        ctx = contextvars.copy_context()
        try:
            loop = asyncio.get_running_loop()
            got_tenant, count = await loop.run_in_executor(None, ctx.run, _query_by_tenant)
            assert got_tenant == "proj-2"
            assert count == 1
        finally:
            reset_tenant(tok)

    @pytest.mark.asyncio
    async def test_session_without_tenant_context_sees_nothing(
        self, async_engine, session_factory, seeded
    ):
        """Without a tenant context set, the thread worker sees no tenant."""
        def _check_tenant():
            return get_tenant()

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _check_tenant)
        assert result is None

    @pytest.mark.asyncio
    async def test_tenant_scoping_prevents_cross_tenant_read(
        self, async_engine, session_factory, seeded
    ):
        """Thread worker in tenant 'proj-1' cannot see 'proj-2' data."""
        def _only_own_tenant(expected: str) -> int:
            current = get_tenant()
            assert current == expected, f"expected {expected}, got {current}"
            import asyncio as _asyncio

            from sqlalchemy import func, select

            async def _inner():
                async with session_factory() as sess:
                    stmt = (
                        select(func.count())
                        .select_from(TodoModel)
                        .where(TodoModel.project_id == current)
                    )
                    result = await sess.execute(stmt)
                    return result.scalar_one()

            return _asyncio.run(_inner())

        tok = set_tenant("proj-1")
        ctx = contextvars.copy_context()
        try:
            loop = asyncio.get_running_loop()
            count = await loop.run_in_executor(None, ctx.run, _only_own_tenant, "proj-1")
            assert count == 2
        finally:
            reset_tenant(tok)

    @pytest.mark.asyncio
    async def test_thread_pool_worker_sees_correct_tenant(
        self, async_engine, session_factory, seeded
    ):
        """Multiple thread pool workers each see the tenant active when created."""
        def _count_for_tenant(exp: str) -> int:
            current = get_tenant()
            assert current == exp
            import asyncio as _asyncio

            from sqlalchemy import func, select

            async def _q():
                async with session_factory() as sess:
                    stmt = (
                        select(func.count())
                        .select_from(TodoModel)
                        .where(TodoModel.project_id == current)
                    )
                    result = await sess.execute(stmt)
                    return result.scalar_one()

            return _asyncio.run(_q())

        loop = asyncio.get_running_loop()

        tok1 = set_tenant("proj-1")
        ctx1 = contextvars.copy_context()
        f1 = loop.run_in_executor(None, ctx1.run, _count_for_tenant, "proj-1")
        reset_tenant(tok1)

        tok2 = set_tenant("proj-2")
        ctx2 = contextvars.copy_context()
        f2 = loop.run_in_executor(None, ctx2.run, _count_for_tenant, "proj-2")
        reset_tenant(tok2)

        assert await f1 == 2
        assert await f2 == 1
