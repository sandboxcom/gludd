"""Tests for TodoRepository.update() immutable-field whitelist (security finding #10).

Mirrors the async-session fixture pattern from test_repository_create_guard.py.
asyncio_mode = "auto" in pyproject.toml — no @pytest.mark.asyncio needed.

Security contract
-----------------
``update()`` must reject mass-assignment of identity / tenant / audit fields:

- ``project_id`` is the tenant scope — allowing it here would permit a
  cross-tenant escape (move a todo into another tenant's namespace).
- ``todo_id`` is the application-assigned business key — changing it swaps
  the entity's identity.
- ``id`` is the DB primary key; ``version``/``updated_at`` are managed by
  ``update()`` itself via the expected_version protocol.
- ``created_at`` / ``created_by`` are set-once audit attribution.

Note: these fields are legitimately caller-supplied at *create* time
(``todo_id``/``project_id`` are in ALLOWED_TODO_CREATE_FIELDS and are
intentionally absent from the create-time ``_IMMUTABLE_FIELDS`` set — see
test_create_immutable_fields_constant_covers_required_set). Create-time and
update-time have different immutability semantics, so they are guarded by
separate sets.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from general_ludd.db.models import Base
from general_ludd.db.repository import TodoRepository

# ---------------------------------------------------------------------------
# Shared async-engine / session fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def async_engine():
    # No PRAGMA foreign_keys here: we intentionally seed tenant-scoped todos
    # without parent project rows so we can exercise the project_id guard
    # (mirrors tests/unit/test_todo_repository_security.py's fixture).
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
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
# FIX: update() immutable-field whitelist (security finding #10)
# ---------------------------------------------------------------------------

# Fields that must NEVER be accepted in an update() payload. ``project_id`` is
# the tenant scope (cross-tenant escape), ``todo_id`` is the business key
# (identity swap), the rest are DB-managed or set-once audit columns.
_REQUIRED_IMMUTABLE_UPDATE_FIELDS = frozenset(
    {
        "id",
        "todo_id",
        "project_id",
        "version",
        "created_at",
        "updated_at",
        "created_by",
    }
)


class TestUpdateImmutableFields:
    """update() must reject identity / tenant / audit fields (finding #10)."""

    async def test_update_immutable_fields_constant_covers_required_set(self):
        """_IMMUTABLE_UPDATE_FIELDS must contain every identity/tenant/audit
        field named in the security fix."""
        assert (
            _REQUIRED_IMMUTABLE_UPDATE_FIELDS
            <= TodoRepository._IMMUTABLE_UPDATE_FIELDS
        )

    async def test_update_rejects_immutable_field_project_id(
        self, async_session: AsyncSession
    ):
        """project_id is the tenant scope — reassigning it is a cross-tenant
        escape and must be rejected."""
        repo = TodoRepository(async_session)
        todo = await repo.create({"title": "seed", "project_id": "tenant-A"})
        with pytest.raises(ValueError, match="immutable"):
            await repo.update(
                todo.todo_id,
                {"project_id": "tenant-B"},
                expected_version=1,
            )

    async def test_update_rejects_immutable_field_todo_id(
        self, async_session: AsyncSession
    ):
        """todo_id is the business identifier — changing it swaps identity."""
        repo = TodoRepository(async_session)
        todo = await repo.create({"title": "seed"})
        with pytest.raises(ValueError, match="immutable"):
            await repo.update(
                todo.todo_id,
                {"todo_id": "TODO-IMPOSTOR"},
                expected_version=1,
            )

    async def test_update_rejects_immutable_field_id(
        self, async_session: AsyncSession
    ):
        """id is the DB primary key — must never be reassignable."""
        repo = TodoRepository(async_session)
        todo = await repo.create({"title": "seed"})
        with pytest.raises(ValueError, match="immutable"):
            await repo.update(todo.todo_id, {"id": 999}, expected_version=1)

    async def test_update_rejects_immutable_field_version(
        self, async_session: AsyncSession
    ):
        """version is managed by the expected_version protocol — a caller
        setting it directly would defeat optimistic-concurrency control."""
        repo = TodoRepository(async_session)
        todo = await repo.create({"title": "seed"})
        with pytest.raises(ValueError, match="immutable"):
            await repo.update(
                todo.todo_id, {"version": 4242}, expected_version=1
            )

    async def test_update_rejects_immutable_field_created_at(
        self, async_session: AsyncSession
    ):
        """created_at is the audit origin timestamp — immutable."""
        repo = TodoRepository(async_session)
        todo = await repo.create({"title": "seed"})
        with pytest.raises(ValueError, match="immutable"):
            await repo.update(
                todo.todo_id,
                {"created_at": "1970-01-01T00:00:00+00:00"},
                expected_version=1,
            )

    async def test_update_rejects_immutable_field_updated_at(
        self, async_session: AsyncSession
    ):
        """updated_at is set by update() itself — caller-supplied values would
        forge the modification audit trail."""
        repo = TodoRepository(async_session)
        todo = await repo.create({"title": "seed"})
        with pytest.raises(ValueError, match="immutable"):
            await repo.update(
                todo.todo_id,
                {"updated_at": "1970-01-01T00:00:00+00:00"},
                expected_version=1,
            )

    async def test_update_rejects_immutable_field_created_by(
        self, async_session: AsyncSession
    ):
        """created_by is set-once audit attribution — immutable."""
        repo = TodoRepository(async_session)
        todo = await repo.create({"title": "seed", "created_by": "alice"})
        with pytest.raises(ValueError, match="immutable"):
            await repo.update(
                todo.todo_id, {"created_by": "mallory"}, expected_version=1
            )

    async def test_update_rejects_multiple_immutable_fields(
        self, async_session: AsyncSession
    ):
        """Multiple immutable fields in one payload must all be rejected."""
        repo = TodoRepository(async_session)
        todo = await repo.create({"title": "seed", "project_id": "tenant-A"})
        with pytest.raises(ValueError, match="immutable"):
            await repo.update(
                todo.todo_id,
                {"project_id": "tenant-B", "todo_id": "TODO-X", "version": 9},
                expected_version=1,
            )

    async def test_update_rejects_immutable_field_without_touching_db(
        self, async_session: AsyncSession
    ):
        """The guard must fire BEFORE any DB write so a rejected update leaves
        the row unchanged."""
        repo = TodoRepository(async_session)
        todo = await repo.create({"title": "seed", "project_id": "tenant-A"})
        original_version = todo.version
        with pytest.raises(ValueError):
            await repo.update(
                todo.todo_id,
                {"project_id": "tenant-B", "title": "mutated"},
                expected_version=1,
            )
        # Row identity / tenant must be unchanged after the rejected call.
        refreshed = await repo.get_by_id(todo.todo_id)
        assert refreshed is not None
        assert refreshed.project_id == "tenant-A"
        assert refreshed.title == "seed"
        assert refreshed.version == original_version

    async def test_update_accepts_mutable_field_status(
        self, async_session: AsyncSession
    ):
        """status is a legitimate mutable field — it MUST remain updatable.
        Guards against the whitelist accidentally over-restricting."""
        repo = TodoRepository(async_session)
        todo = await repo.create({"title": "seed"})
        updated = await repo.update(
            todo.todo_id, {"status": "queued"}, expected_version=1
        )
        assert updated.status == "queued"
        assert updated.version == 2

    async def test_update_accepts_mutable_field_title(
        self, async_session: AsyncSession
    ):
        """title is a legitimate mutable field."""
        repo = TodoRepository(async_session)
        todo = await repo.create({"title": "seed"})
        updated = await repo.update(
            todo.todo_id, {"title": "renamed"}, expected_version=1
        )
        assert updated.title == "renamed"
