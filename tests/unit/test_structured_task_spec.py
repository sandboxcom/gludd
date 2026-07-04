"""G13: Structured task-spec / acceptance_criteria + definition_of_done."""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from general_ludd.db.models import Base
from general_ludd.db.repository import TodoRepository
from general_ludd.schemas.todo import TodoStatus


@pytest_asyncio.fixture()
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _ac_from_json(raw: str) -> list[str]:
    """Deserialize acceptance_criteria from DB JSON string."""
    return json.loads(raw) if raw else []


# ── Todo creation with acceptance_criteria ─────────────────────────────

class TestTodoCreationWithAcceptanceCriteria:
    @pytest.mark.asyncio
    async def test_create_todo_with_acceptance_criteria(self, session: AsyncSession):
        repo = TodoRepository(session)
        await repo.create(
            todo_data={
                "todo_id": "TODO-G13-01",
                "title": "Implement G13",
                "status": TodoStatus.BACKLOG.value,
                "acceptance_criteria": json.dumps(["Must pass tests", "Must be reviewed"]),
            }
        )
        await session.commit()

        fetched = await repo.get_by_id("TODO-G13-01")
        assert fetched is not None
        criteria = _ac_from_json(fetched.acceptance_criteria)
        assert len(criteria) == 2
        assert "Must pass tests" in criteria
        assert "Must be reviewed" in criteria


# ── Todo creation with definition_of_done ──────────────────────────────

class TestTodoCreationWithDefinitionOfDone:
    @pytest.mark.asyncio
    async def test_create_todo_with_definition_of_done(self, session: AsyncSession):
        repo = TodoRepository(session)
        await repo.create(
            todo_data={
                "todo_id": "TODO-G13-02",
                "title": "Implement DOD",
                "status": TodoStatus.BACKLOG.value,
                "definition_of_done": "All tests pass and code is deployed",
            }
        )
        await session.commit()

        fetched = await repo.get_by_id("TODO-G13-02")
        assert fetched is not None
        assert fetched.definition_of_done == "All tests pass and code is deployed"


# ── Validation: empty criteria list ────────────────────────────────────

class TestAcceptanceCriteriaValidation:
    @pytest.mark.asyncio
    async def test_empty_acceptance_criteria_list_allowed(self, session: AsyncSession):
        repo = TodoRepository(session)
        await repo.create(
            todo_data={
                "todo_id": "TODO-G13-03",
                "title": "Empty criteria",
                "status": TodoStatus.BACKLOG.value,
                "acceptance_criteria": "[]",
            }
        )
        await session.commit()

        fetched = await repo.get_by_id("TODO-G13-03")
        assert fetched is not None
        criteria = _ac_from_json(fetched.acceptance_criteria)
        assert criteria == []


# ── Validation: criteria items min 3 chars each ────────────────────────

class TestCriteriaItemMinLength:
    @pytest.mark.asyncio
    async def test_acceptance_criteria_items_min_3_chars(self, session: AsyncSession):
        repo = TodoRepository(session)
        await repo.create(
            todo_data={
                "todo_id": "TODO-G13-04",
                "title": "Short criteria items",
                "status": TodoStatus.BACKLOG.value,
                "acceptance_criteria": json.dumps(["OK", "Fine", "Yes"]),
            }
        )
        await session.commit()

        fetched = await repo.get_by_id("TODO-G13-04")
        assert fetched is not None
        criteria = _ac_from_json(fetched.acceptance_criteria)
        for item in criteria:
            assert len(item) >= 3, f"Criteria item {item!r} must be at least 3 chars"


# ── Repository filters by acceptance_criteria presence ─────────────────

class TestFilterByAcceptanceCriteriaPresence:
    @pytest.mark.asyncio
    async def test_repo_filters_by_acceptance_criteria_presence(self, session: AsyncSession):
        repo = TodoRepository(session)

        await repo.create(
            todo_data={
                "todo_id": "TODO-G13-WITH",
                "title": "With criteria",
                "status": TodoStatus.BACKLOG.value,
                "acceptance_criteria": json.dumps(["Must be done"]),
            }
        )
        await repo.create(
            todo_data={
                "todo_id": "TODO-G13-WITHOUT",
                "title": "Without criteria",
                "status": TodoStatus.BACKLOG.value,
                "acceptance_criteria": "[]",
            }
        )
        await session.commit()

        all_todos = await repo.list_by_status(TodoStatus.BACKLOG)
        with_criteria = [
            t for t in all_todos
            if _ac_from_json(t.acceptance_criteria)
        ]
        without_criteria = [
            t for t in all_todos
            if not _ac_from_json(t.acceptance_criteria)
        ]

        assert any(t.todo_id == "TODO-G13-WITH" for t in with_criteria)
        assert any(t.todo_id == "TODO-G13-WITHOUT" for t in without_criteria)
        assert not any(t.todo_id == "TODO-G13-WITH" for t in without_criteria)
        assert not any(t.todo_id == "TODO-G13-WITHOUT" for t in with_criteria)
