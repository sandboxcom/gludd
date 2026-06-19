"""TDD tests for D-27 (tenant-scoped TodoRepository) and D-28 (create field
allowlist + text cap).

D-27
----
- ``TodoRepository.scoped(session, project_id)`` returns a repo pre-scoped to
  that project.
- All query methods honour the instance scope: rows belonging to *other*
  projects are excluded even when the caller omits ``project_id``.
- An unscoped repo (plain constructor) still returns rows from all projects
  (admin path unchanged).

D-28
----
- ``create()`` rejects any payload that contains immutable DB-assigned fields
  (``id``, ``version``, ``created_at``, ``updated_at``).
- ``create()`` accepts ``todo_id`` as an app-assignable business key.
- ``create()`` rejects text field values that exceed ``_MAX_TEXT_BYTES`` bytes.
- ``create()`` always sets ``version=1`` on the created row.
- A valid minimal payload (title only) succeeds and sets version=1.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from general_ludd.db.models import Base, TodoModel
from general_ludd.db.repository import TodoRepository
from general_ludd.schemas.todo import TodoStatus

# ---------------------------------------------------------------------------
# Shared async DB fixture
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture()
async def session():
    """In-memory SQLite session — full schema, fresh per test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _seed_todo(
    session: AsyncSession,
    project_id: str | None,
    title: str = "seed",
    status: str = TodoStatus.BACKLOG.value,
) -> TodoModel:
    """Insert a todo row directly (bypassing TodoRepository.create) so we can
    seed rows without touching the allowlist guard under test."""
    todo = TodoModel(
        project_id=project_id,
        title=title,
        status=status,
    )
    session.add(todo)
    await session.flush()
    return todo


# ===========================================================================
# D-27: scoped() classmethod + instance-level project_id
# ===========================================================================

class TestScopedClassmethod:
    """TodoRepository.scoped(session, project_id) returns a repo bound to
    that project.  Every read method must exclude rows from other projects
    even when the caller passes project_id=None."""

    async def test_scoped_returns_todo_repository_instance(self, session: AsyncSession):
        repo = TodoRepository.scoped(session, "proj-A")
        assert isinstance(repo, TodoRepository)
        assert repo._project_id == "proj-A"

    async def test_unscoped_constructor_has_none_project_id(self, session: AsyncSession):
        repo = TodoRepository(session)
        assert repo._project_id is None

    @pytest.mark.asyncio
    async def test_scoped_get_by_id_excludes_other_project(self, session: AsyncSession):
        """A scoped repo must NOT see a todo belonging to a different project."""
        todo_b = await _seed_todo(session, project_id="proj-B", title="other")
        repo = TodoRepository.scoped(session, "proj-A")
        result = await repo.get_by_id(todo_b.todo_id)
        assert result is None

    @pytest.mark.asyncio
    async def test_scoped_get_by_id_finds_own_project(self, session: AsyncSession):
        todo_a = await _seed_todo(session, project_id="proj-A", title="mine")
        repo = TodoRepository.scoped(session, "proj-A")
        result = await repo.get_by_id(todo_a.todo_id)
        assert result is not None
        assert result.todo_id == todo_a.todo_id

    @pytest.mark.asyncio
    async def test_unscoped_get_by_id_returns_any_project(self, session: AsyncSession):
        """Unscoped (admin) repo must be able to reach rows from any project."""
        todo_b = await _seed_todo(session, project_id="proj-B", title="other")
        repo = TodoRepository(session)
        result = await repo.get_by_id(todo_b.todo_id)
        assert result is not None

    @pytest.mark.asyncio
    async def test_scoped_list_all_excludes_other_project(self, session: AsyncSession):
        await _seed_todo(session, project_id="proj-A", title="mine")
        await _seed_todo(session, project_id="proj-B", title="not-mine")
        repo = TodoRepository.scoped(session, "proj-A")
        rows = await repo.list_all()
        titles = [r.title for r in rows]
        assert "mine" in titles
        assert "not-mine" not in titles

    @pytest.mark.asyncio
    async def test_unscoped_list_all_returns_all_projects(self, session: AsyncSession):
        await _seed_todo(session, project_id="proj-A", title="a")
        await _seed_todo(session, project_id="proj-B", title="b")
        repo = TodoRepository(session)
        rows = await repo.list_all()
        titles = [r.title for r in rows]
        assert "a" in titles
        assert "b" in titles

    @pytest.mark.asyncio
    async def test_scoped_list_by_status_excludes_other_project(self, session: AsyncSession):
        await _seed_todo(session, project_id="proj-A", title="mine", status=TodoStatus.BACKLOG.value)
        await _seed_todo(session, project_id="proj-B", title="other", status=TodoStatus.BACKLOG.value)
        repo = TodoRepository.scoped(session, "proj-A")
        rows = await repo.list_by_status(TodoStatus.BACKLOG)
        titles = [r.title for r in rows]
        assert "mine" in titles
        assert "other" not in titles

    @pytest.mark.asyncio
    async def test_scoped_list_by_work_type_excludes_other_project(self, session: AsyncSession):
        a = await _seed_todo(session, project_id="proj-A", title="mine")
        a.work_type = "code"
        await session.flush()
        b = await _seed_todo(session, project_id="proj-B", title="other")
        b.work_type = "code"
        await session.flush()
        repo = TodoRepository.scoped(session, "proj-A")
        rows = await repo.list_by_work_type("code")
        titles = [r.title for r in rows]
        assert "mine" in titles
        assert "other" not in titles

    @pytest.mark.asyncio
    async def test_scoped_count_active_excludes_other_project(self, session: AsyncSession):
        await _seed_todo(session, project_id="proj-A", title="mine", status=TodoStatus.ACTIVE.value)
        await _seed_todo(session, project_id="proj-B", title="other", status=TodoStatus.ACTIVE.value)
        repo_a = TodoRepository.scoped(session, "proj-A")
        count = await repo_a.count_active()
        assert count == 1

    @pytest.mark.asyncio
    async def test_scoped_status_summary_excludes_other_project(self, session: AsyncSession):
        await _seed_todo(session, project_id="proj-A", title="mine")
        await _seed_todo(session, project_id="proj-B", title="other")
        repo = TodoRepository.scoped(session, "proj-A")
        summary = await repo.status_summary()
        assert summary["total"] == 1

    @pytest.mark.asyncio
    async def test_explicit_project_id_overrides_instance_scope(self, session: AsyncSession):
        """An explicit project_id kwarg takes precedence over the instance scope."""
        todo_b = await _seed_todo(session, project_id="proj-B", title="other")
        # Scoped to proj-A but explicitly ask for proj-B
        repo = TodoRepository.scoped(session, "proj-A")
        result = await repo.get_by_id(todo_b.todo_id, project_id="proj-B")
        assert result is not None

    @pytest.mark.asyncio
    async def test_resolve_pid_explicit_wins_over_instance(self, session: AsyncSession):
        repo = TodoRepository.scoped(session, "proj-A")
        assert repo._resolve_pid("proj-X") == "proj-X"

    @pytest.mark.asyncio
    async def test_resolve_pid_instance_used_when_none_passed(self, session: AsyncSession):
        repo = TodoRepository.scoped(session, "proj-A")
        assert repo._resolve_pid(None) == "proj-A"

    @pytest.mark.asyncio
    async def test_resolve_pid_none_none_gives_none(self, session: AsyncSession):
        repo = TodoRepository(session)
        assert repo._resolve_pid(None) is None


# ===========================================================================
# D-28: create() field allowlist + text cap
# ===========================================================================

class TestCreateFieldAllowlist:
    """create() must reject immutable fields and oversized text."""

    @pytest.mark.asyncio
    async def test_create_rejects_id_field(self, session: AsyncSession):
        repo = TodoRepository(session)
        with pytest.raises(ValueError, match="immutable"):
            await repo.create({"title": "t", "id": 999})

    @pytest.mark.asyncio
    async def test_create_accepts_todo_id_field(self, session: AsyncSession):
        """todo_id is an app-assigned business key — callers may supply it at
        create time; only the DB PK (id) is immutable/forbidden."""
        repo = TodoRepository(session)
        todo = await repo.create({"title": "t", "todo_id": "TODO-EXPLICIT"})
        assert todo.todo_id == "TODO-EXPLICIT"

    @pytest.mark.asyncio
    async def test_create_rejects_version_field(self, session: AsyncSession):
        repo = TodoRepository(session)
        with pytest.raises(ValueError, match="immutable"):
            await repo.create({"title": "t", "version": 99})

    @pytest.mark.asyncio
    async def test_create_rejects_created_at_field(self, session: AsyncSession):
        repo = TodoRepository(session)
        with pytest.raises(ValueError, match="immutable"):
            await repo.create({"title": "t", "created_at": datetime.now(UTC)})

    @pytest.mark.asyncio
    async def test_create_rejects_updated_at_field(self, session: AsyncSession):
        repo = TodoRepository(session)
        with pytest.raises(ValueError, match="immutable"):
            await repo.create({"title": "t", "updated_at": datetime.now(UTC)})

    @pytest.mark.asyncio
    async def test_create_rejects_oversized_title(self, session: AsyncSession):
        repo = TodoRepository(session)
        big = "x" * (TodoRepository._MAX_TEXT_BYTES + 1)
        with pytest.raises(ValueError, match="65536"):
            await repo.create({"title": big})

    @pytest.mark.asyncio
    async def test_create_rejects_oversized_description(self, session: AsyncSession):
        repo = TodoRepository(session)
        big = "y" * (TodoRepository._MAX_TEXT_BYTES + 1)
        with pytest.raises(ValueError, match="65536"):
            await repo.create({"title": "ok", "description": big})

    @pytest.mark.asyncio
    async def test_create_allows_title_at_exactly_max_bytes(self, session: AsyncSession):
        repo = TodoRepository(session)
        exactly = "z" * TodoRepository._MAX_TEXT_BYTES
        # Must NOT raise
        todo = await repo.create({"title": exactly})
        assert todo.todo_id is not None

    @pytest.mark.asyncio
    async def test_create_sets_version_to_1(self, session: AsyncSession):
        """create() must always persist version=1 on the new row."""
        repo = TodoRepository(session)
        todo = await repo.create({"title": "fresh"})
        assert todo.version == 1

    @pytest.mark.asyncio
    async def test_create_valid_minimal_payload_succeeds(self, session: AsyncSession):
        repo = TodoRepository(session)
        todo = await repo.create({"title": "hello"})
        assert todo.todo_id is not None
        assert todo.version == 1
        assert todo.title == "hello"

    @pytest.mark.asyncio
    async def test_create_immutable_fields_constant_covers_required_set(self):
        """_IMMUTABLE_FIELDS must contain the DB-assigned fields; todo_id is
        intentionally excluded because callers may supply it as a business key."""
        required = {"id", "version", "created_at", "updated_at"}
        assert required <= TodoRepository._IMMUTABLE_FIELDS
        # todo_id must NOT be in _IMMUTABLE_FIELDS — it is an app-assignable key
        assert "todo_id" not in TodoRepository._IMMUTABLE_FIELDS
