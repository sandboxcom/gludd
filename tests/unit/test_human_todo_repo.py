"""Unit tests for HumanTodoModel + HumanTodoRepository (bot→human requests).

Uses SQLite in-memory with async sessions via aiosqlite, mirroring
tests/unit/test_agent_message_repo.py.
"""

from __future__ import annotations

import json as _json

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base, HumanTodoModel
from general_ludd.db.repository import (
    HUMAN_TODO_TERMINAL,
    HumanTodoRepository,
    InvalidTransitionError,
)


def _make_async_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
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
    session_factory = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


class TestHumanTodoRepository:
    @pytest.mark.asyncio
    async def test_create_returns_id(self, async_session: AsyncSession):
        repo = HumanTodoRepository(async_session)
        row = await repo.create(
            agent_id="agent-1",
            title="Need prod key",
            body="OPENAI_API_KEY is missing",
            category="input_request",
            priority="urgent",
        )
        await async_session.flush()
        assert row.id is not None and row.id.startswith("HTODO-")

    @pytest.mark.asyncio
    async def test_get_returns_full_record(self, async_session: AsyncSession):
        repo = HumanTodoRepository(async_session)
        created = await repo.create(
            agent_id="agent-1",
            title="t",
            body="b",
            category="blocker",
        )
        await async_session.flush()
        fetched = await repo.get(created.id)
        assert fetched is not None
        assert fetched.title == "t"
        assert fetched.body == "b"
        assert fetched.status == "open"

    @pytest.mark.asyncio
    async def test_list_open_filters_by_category(self, async_session: AsyncSession):
        repo = HumanTodoRepository(async_session)
        await repo.create(agent_id="a", title="t1", body="b", category="permission_escalation")
        await repo.create(agent_id="a", title="t2", body="b", category="decision")
        await repo.create(agent_id="a", title="t3", body="b", category="decision")
        await async_session.flush()
        decisions = await repo.list_open(filter_category="decision")
        assert len(decisions) == 2

    @pytest.mark.asyncio
    async def test_mark_done_sets_resolution_and_resolver(self, async_session: AsyncSession):
        repo = HumanTodoRepository(async_session)
        row = await repo.create(agent_id="a", title="t", body="b", category="blocker")
        await async_session.flush()
        done = await repo.mark_done(row.id, "shawn", "key rotated")
        assert done.status == "done"
        assert done.human_resolution == "key rotated"
        assert done.human_resolver == "shawn"
        assert done.resolved_at is not None

    @pytest.mark.asyncio
    async def test_mark_done_is_terminal(self, async_session: AsyncSession):
        repo = HumanTodoRepository(async_session)
        row = await repo.create(agent_id="a", title="t", body="b", category="blocker")
        await async_session.flush()
        await repo.mark_done(row.id, "shawn", "done")
        with pytest.raises(InvalidTransitionError):
            await repo.mark_in_progress(row.id)

    @pytest.mark.asyncio
    async def test_dismiss_sets_reason(self, async_session: AsyncSession):
        repo = HumanTodoRepository(async_session)
        row = await repo.create(agent_id="a", title="t", body="b", category="decision")
        await async_session.flush()
        dismissed = await repo.dismiss(row.id, "shawn", "won't do this")
        assert dismissed.status == "dismissed"
        assert dismissed.human_resolution == "won't do this"
        assert dismissed.human_resolver == "shawn"
        assert row.status in HUMAN_TODO_TERMINAL

    @pytest.mark.asyncio
    async def test_status_transitions_enforced(self, async_session: AsyncSession):
        repo = HumanTodoRepository(async_session)
        row = await repo.create(agent_id="a", title="t", body="b", category="blocker")
        await async_session.flush()
        # open -> done is valid
        await repo.mark_done(row.id, "h", "ok")
        # done -> open is rejected
        with pytest.raises(InvalidTransitionError):
            await repo._transition(row.id, "open")

    @pytest.mark.asyncio
    async def test_tags_add_remove(self, async_session: AsyncSession):
        repo = HumanTodoRepository(async_session)
        row = await repo.create(agent_id="a", title="t", body="b", category="blocker")
        await async_session.flush()
        await repo.add_tag(row.id, "perm")
        await repo.add_tag(row.id, "task:X")
        await repo.add_tag(row.id, "perm")  # idempotent
        fetched = await repo.get(row.id)
        tags = _json.loads(fetched.tags)
        assert sorted(tags) == ["perm", "task:X"]
        await repo.remove_tag(row.id, "perm")
        fetched = await repo.get(row.id)
        assert _json.loads(fetched.tags) == ["task:X"]

    @pytest.mark.asyncio
    async def test_search_matches_title_and_body(self, async_session: AsyncSession):
        repo = HumanTodoRepository(async_session)
        await repo.create(
            agent_id="a",
            title="Need OPENAI_API_KEY",
            body="some text",
            category="input_request",
        )
        await repo.create(
            agent_id="a",
            title="other",
            body="talks about OPENAI_API_KEY in body",
            category="blocker",
        )
        await repo.create(agent_id="a", title="unrelated", body="nothing", category="blocker")
        await async_session.flush()
        matches = await repo.search("OPENAI_API_KEY")
        assert len(matches) == 2

    @pytest.mark.asyncio
    async def test_invalid_category_rejected(self, async_session: AsyncSession):
        repo = HumanTodoRepository(async_session)
        with pytest.raises(ValueError):
            await repo.create(agent_id="a", title="t", body="b", category="nonsense")

    @pytest.mark.asyncio
    async def test_empty_title_rejected(self, async_session: AsyncSession):
        repo = HumanTodoRepository(async_session)
        with pytest.raises(ValueError):
            await repo.create(agent_id="a", title="  ", body="b", category="blocker")

    @pytest.mark.asyncio
    async def test_mark_in_progress_from_open(self, async_session: AsyncSession):
        repo = HumanTodoRepository(async_session)
        row = await repo.create(agent_id="a", title="t", body="b", category="blocker")
        await async_session.flush()
        ip = await repo.mark_in_progress(row.id)
        assert ip.status == "in_progress"

    @pytest.mark.asyncio
    async def test_supersede_records_new_id(self, async_session: AsyncSession):
        repo = HumanTodoRepository(async_session)
        old = await repo.create(agent_id="a", title="t", body="b", category="blocker")
        await async_session.flush()
        sup = await repo.supersede(old.id, "HTODO-NEW1", "re-scoped")
        assert sup.status == "superseded"
        assert "HTODO-NEW1" in (sup.human_resolution or "")


class TestHumanTodoModel:
    def test_table_name(self):
        assert HumanTodoModel.__tablename__ == "human_todos"
