"""S.6: Task type substring false-positive guard tests.

When task_types is stored as a JSON string literal (e.g. '"game_building"')
rather than a JSON array, json.loads returns a bare string and the Python
``in`` operator does substring matching, not membership testing. This
causes false positives — "game" matches "game_building".

The fix normalizes the deserialized value to a list before the ``in`` check.
"""
from __future__ import annotations

import pytest_asyncio
import sqlalchemy
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from general_ludd.db.models import Base, PromptProfileModel
from general_ludd.db.repository import PromptProfileRepository


def _make_async_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    @sqlalchemy.event.listens_for(engine.sync_engine, "connect")
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


class TestS6TaskTypeSubstringFalsePositives:
    """Substring false-positives when task_types is a JSON string literal."""

    @staticmethod
    async def _add(session: AsyncSession, name: str, task_types: str) -> None:
        session.add(
            PromptProfileModel(
                name=name,
                source="test",
                prompt_text="x",
                task_types=task_types,
            )
        )

    async def test_bare_json_string_no_substring_match(
        self, async_session: AsyncSession
    ):
        """'game' must NOT match 'game_building' stored as a bare JSON string."""
        await self._add(async_session, "p_game_building", '"game_building"')
        await async_session.flush()
        repo = PromptProfileRepository(async_session)
        names = {p.name for p in await repo.list_for_task_type("game")}
        assert "p_game_building" not in names

    async def test_bare_json_string_exact_match(self, async_session: AsyncSession):
        """'game_building' must match itself as a bare JSON string."""
        await self._add(async_session, "p_game_building", '"game_building"')
        await async_session.flush()
        repo = PromptProfileRepository(async_session)
        names = {p.name for p in await repo.list_for_task_type("game_building")}
        assert "p_game_building" in names

    async def test_percent_sign_no_match_on_bare_string(
        self, async_session: AsyncSession
    ):
        """'%' must not match everything when task_types is a bare string."""
        await self._add(async_session, "p_any", '"any_task"')
        await async_session.flush()
        repo = PromptProfileRepository(async_session)
        names = {p.name for p in await repo.list_for_task_type("%")}
        assert "p_any" not in names

    async def test_bare_string_in_mixed_data(self, async_session: AsyncSession):
        """Exact match still works alongside bare-string profiles."""
        await self._add(async_session, "p_array", '["game", "feature"]')
        await self._add(async_session, "p_bare", '"game_building"')
        await async_session.flush()
        repo = PromptProfileRepository(async_session)
        names = {p.name for p in await repo.list_for_task_type("game")}
        assert "p_array" in names
        assert "p_bare" not in names

    async def test_bare_json_number_is_match_all(
        self, async_session: AsyncSession
    ):
        """Non-list/non-string JSON values fall back to match-all (safe default)."""
        await self._add(async_session, "p_num", "42")
        await async_session.flush()
        repo = PromptProfileRepository(async_session)
        names = {p.name for p in await repo.list_for_task_type("anything")}
        assert "p_num" in names

    async def test_empty_json_list_still_matches_all(
        self, async_session: AsyncSession
    ):
        """Empty list [] still acts as match-all (preserve existing behavior)."""
        await self._add(async_session, "p_empty", "[]")
        await async_session.flush()
        repo = PromptProfileRepository(async_session)
        names = {p.name for p in await repo.list_for_task_type("anything")}
        assert "p_empty" in names

    async def test_exact_list_match_still_works(self, async_session: AsyncSession):
        """Normal JSON array matching is unaffected by the normalization fix."""
        await self._add(async_session, "p_code", '["code", "docs"]')
        await async_session.flush()
        repo = PromptProfileRepository(async_session)
        names = {p.name for p in await repo.list_for_task_type("code")}
        assert "p_code" in names

    async def test_malformed_json_still_matches_all(
        self, async_session: AsyncSession
    ):
        """Malformed JSON still acts as match-all (preserve existing behavior)."""
        await self._add(async_session, "p_bad", "{not json")
        await async_session.flush()
        repo = PromptProfileRepository(async_session)
        names = {p.name for p in await repo.list_for_task_type("anything")}
        assert "p_bad" in names
