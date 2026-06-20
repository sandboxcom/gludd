"""Tests for TodoRepository.create() field-allowlist + text-size cap (batch-5).

Mirrors the async-session fixture pattern from test_db_models.py.
asyncio_mode = "auto" in pyproject.toml — no @pytest.mark.asyncio needed.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from general_ludd.db.models import Base
from general_ludd.db.repository import TodoRepository

# ---------------------------------------------------------------------------
# Shared async-engine / session fixtures
# ---------------------------------------------------------------------------

def _make_async_engine():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


@pytest_asyncio.fixture
async def async_engine():
    engine = _make_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def async_session(async_engine) -> AsyncSession:
    session_factory = sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session


# ---------------------------------------------------------------------------
# FIX 2: field-allowlist guard
# ---------------------------------------------------------------------------

class TestCreateFieldAllowlist:
    async def test_normal_create_succeeds(self, async_session: AsyncSession):
        """A normal create with only allowed fields must succeed."""
        repo = TodoRepository(async_session)
        todo = await repo.create({"title": "Normal task", "queue": "core"})
        assert todo.title == "Normal task"
        assert todo.queue == "core"
        assert todo.todo_id.startswith("TODO-")

    async def test_disallowed_field_version_raises(self, async_session: AsyncSession):
        """Passing 'version' (auto-managed) must raise ValueError."""
        repo = TodoRepository(async_session)
        with pytest.raises(ValueError, match="immutable"):
            await repo.create({"title": "Bad task", "version": 99})

    async def test_disallowed_field_id_raises(self, async_session: AsyncSession):
        """Passing 'id' (auto-increment PK) must raise ValueError."""
        repo = TodoRepository(async_session)
        with pytest.raises(ValueError, match="immutable"):
            await repo.create({"title": "Bad task", "id": 1})

    async def test_disallowed_field_not_written(self, async_session: AsyncSession):
        """Disallowed field must not be silently written — ValueError stops the create."""
        repo = TodoRepository(async_session)
        with pytest.raises(ValueError):
            await repo.create({"title": "Ghost", "version": 42})
        # Nothing should have been committed
        todos = await repo.list_all()
        assert all(t.title != "Ghost" for t in todos)

    async def test_multiple_disallowed_fields_raises(self, async_session: AsyncSession):
        """Multiple disallowed fields in one call must raise ValueError."""
        repo = TodoRepository(async_session)
        with pytest.raises(ValueError, match="immutable"):
            await repo.create({"title": "Bad", "id": 1, "version": 2})

    async def test_allowed_fields_all_pass(self, async_session: AsyncSession):
        """A rich but fully-allowed dict must succeed without error."""
        repo = TodoRepository(async_session)
        todo = await repo.create({
            "title": "Rich task",
            "description": "Details here",
            "queue": "core",
            "work_type": "code",
            "priority": 3,
            "project_id": None,
            "plan_artifact": "## Plan\n- step 1",
        })
        assert todo.title == "Rich task"
        assert todo.plan_artifact == "## Plan\n- step 1"


# ---------------------------------------------------------------------------
# FIX 2: text size cap
# ---------------------------------------------------------------------------

class TestCreateSizeCap:
    async def test_oversized_title_raises(self, async_session: AsyncSession):
        """A title exceeding 65536 UTF-8 bytes must raise ValueError."""
        repo = TodoRepository(async_session)
        huge_title = "x" * (65536 + 1)
        with pytest.raises(ValueError, match="exceeds"):
            await repo.create({"title": huge_title})

    async def test_exactly_at_limit_succeeds(self, async_session: AsyncSession):
        """A title of exactly 65536 bytes must succeed."""
        repo = TodoRepository(async_session)
        at_limit = "a" * 65536
        todo = await repo.create({"title": at_limit})
        assert len(todo.title) == 65536

    async def test_oversized_description_raises(self, async_session: AsyncSession):
        """A description exceeding 65536 UTF-8 bytes must raise ValueError."""
        repo = TodoRepository(async_session)
        huge_desc = "y" * (65536 + 1)
        with pytest.raises(ValueError, match="exceeds"):
            await repo.create({"title": "Fine title", "description": huge_desc})

    async def test_normal_size_strings_pass(self, async_session: AsyncSession):
        """Reasonably sized strings must not trigger the size cap."""
        repo = TodoRepository(async_session)
        todo = await repo.create({
            "title": "Short title",
            "description": "Normal description",
        })
        assert todo.title == "Short title"

    async def test_multibyte_utf8_size_measured_correctly(self, async_session: AsyncSession):
        """Size cap counts UTF-8 bytes, not character count.
        A string of 32769 2-byte chars = 65538 bytes > cap -> ValueError."""
        repo = TodoRepository(async_session)
        # U+00E9 (é) is 2 bytes in UTF-8; 32769 of them = 65538 bytes
        multibyte_str = "é" * 32769
        assert len(multibyte_str.encode("utf-8")) > 65536
        with pytest.raises(ValueError, match="exceeds"):
            await repo.create({"title": multibyte_str})


# ---------------------------------------------------------------------------
# FIX 1: optional limit on list_all
# ---------------------------------------------------------------------------

class TestListAllLimit:
    async def test_list_all_no_limit_returns_all(self, async_session: AsyncSession):
        """list_all() with no limit returns all rows (existing behaviour)."""
        repo = TodoRepository(async_session)
        for i in range(5):
            await repo.create({"title": f"Task {i}"})
        results = await repo.list_all()
        assert len(results) >= 5

    async def test_list_all_with_limit(self, async_session: AsyncSession):
        """list_all(limit=2) returns at most 2 rows."""
        repo = TodoRepository(async_session)
        for i in range(5):
            await repo.create({"title": f"LimitTask {i}"})
        results = await repo.list_all(limit=2)
        assert len(results) == 2

    async def test_list_all_limit_zero_returns_empty(self, async_session: AsyncSession):
        """list_all(limit=0) returns empty list."""
        repo = TodoRepository(async_session)
        await repo.create({"title": "Some task"})
        results = await repo.list_all(limit=0)
        assert results == []
