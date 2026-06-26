"""Behavioral tests for P12 unbounded-list-cap fixes.

Each capped repository method is proven to return at most ``_DEFAULT_LIST_LIMIT``
rows (verified by patching the constant to a small sentinel ``_SMALL_CAP``).
The ``/api/status`` handler is proven to use aggregate COUNT queries (via
``TodoRepository.status_summary``) so its reported depths are correct even
when the todo count exceeds the cap.

asyncio_mode = "auto" is set in pyproject.toml — no @pytest.mark.asyncio needed.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.daemon import create_daemon_app
from general_ludd.db.models import Base, BenchmarkResultModel
from general_ludd.db.repository import (
    _DEFAULT_LIST_LIMIT,
    BenchmarkRepository,
    PromptProfileRepository,
    TodoRepository,
    VariableNamespaceRepository,
)
from general_ludd.schemas.todo import TodoStatus

# ---------------------------------------------------------------------------
# Small sentinel cap used when patching _DEFAULT_LIST_LIMIT
# ---------------------------------------------------------------------------

_SMALL_CAP = 3


# ---------------------------------------------------------------------------
# Shared async engine/session fixtures
# ---------------------------------------------------------------------------


def _make_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


@pytest_asyncio.fixture
async def async_engine():
    engine = _make_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def async_session(async_engine) -> AsyncSession:
    factory = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _seed_todos(
    repo: TodoRepository,
    n: int,
    queue: str = "core",
    work_type: str = "code",
) -> None:
    """Create *n* todos in BACKLOG (default) with the given queue/work_type."""
    for i in range(n):
        await repo.create({"title": f"todo-{i}", "queue": queue, "work_type": work_type})


# ---------------------------------------------------------------------------
# TodoRepository.list_by_status — cap enforced
# ---------------------------------------------------------------------------


class TestListByStatusCapped:
    """P12 cap on list_by_status: no explicit limit -> _DEFAULT_LIST_LIMIT applied."""

    async def test_small_cap_returns_at_most_cap_rows(self, async_session: AsyncSession):
        """Patching _DEFAULT_LIST_LIMIT to _SMALL_CAP limits the returned rows."""
        repo = TodoRepository(async_session)
        await _seed_todos(repo, _SMALL_CAP + 2)
        with patch("general_ludd.db.repository._DEFAULT_LIST_LIMIT", _SMALL_CAP):
            result = await repo.list_by_status(TodoStatus.BACKLOG)
        assert len(result) <= _SMALL_CAP

    async def test_explicit_limit_larger_than_cap_is_clamped(
        self, async_session: AsyncSession
    ):
        """An explicit limit > _DEFAULT_LIST_LIMIT is clamped to the cap."""
        repo = TodoRepository(async_session)
        await _seed_todos(repo, _SMALL_CAP + 2)
        with patch("general_ludd.db.repository._DEFAULT_LIST_LIMIT", _SMALL_CAP):
            result = await repo.list_by_status(
                TodoStatus.BACKLOG, limit=_SMALL_CAP + 100
            )
        assert len(result) <= _SMALL_CAP

    async def test_explicit_limit_below_cap_is_respected(
        self, async_session: AsyncSession
    ):
        """An explicit limit smaller than the cap is honored as-is."""
        repo = TodoRepository(async_session)
        await _seed_todos(repo, _SMALL_CAP + 2)
        result = await repo.list_by_status(TodoStatus.BACKLOG, limit=1)
        assert len(result) == 1

    async def test_default_cap_constant_is_1000(self):
        """Sanity: the module-level cap is 1 000 rows."""
        assert _DEFAULT_LIST_LIMIT == 1000


# ---------------------------------------------------------------------------
# TodoRepository.list_all — cap enforced
# ---------------------------------------------------------------------------


class TestListAllCapped:
    """P12 cap on list_all (the public-path method — Defect 1 surface)."""

    async def test_small_cap_returns_at_most_cap_rows(self, async_session: AsyncSession):
        repo = TodoRepository(async_session)
        await _seed_todos(repo, _SMALL_CAP + 2)
        with patch("general_ludd.db.repository._DEFAULT_LIST_LIMIT", _SMALL_CAP):
            result = await repo.list_all()
        assert len(result) <= _SMALL_CAP

    async def test_explicit_limit_larger_than_cap_is_clamped(
        self, async_session: AsyncSession
    ):
        repo = TodoRepository(async_session)
        await _seed_todos(repo, _SMALL_CAP + 2)
        with patch("general_ludd.db.repository._DEFAULT_LIST_LIMIT", _SMALL_CAP):
            result = await repo.list_all(limit=_SMALL_CAP + 100)
        assert len(result) <= _SMALL_CAP

    async def test_explicit_limit_below_cap_is_respected(
        self, async_session: AsyncSession
    ):
        repo = TodoRepository(async_session)
        await _seed_todos(repo, _SMALL_CAP + 2)
        result = await repo.list_all(limit=2)
        assert len(result) == 2

    async def test_queue_filter_applied_before_cap(self, async_session: AsyncSession):
        """Queue filter is applied in SQL before the LIMIT fires."""
        repo = TodoRepository(async_session)
        # _SMALL_CAP + 1 rows in "core", 2 rows in "other"
        await _seed_todos(repo, _SMALL_CAP + 1, queue="core")
        await _seed_todos(repo, 2, queue="other")
        with patch("general_ludd.db.repository._DEFAULT_LIST_LIMIT", _SMALL_CAP):
            # Filtering on "other" (only 2 rows) must return exactly 2
            result = await repo.list_all(queue="other")
        assert len(result) == 2


# ---------------------------------------------------------------------------
# TodoRepository.list_by_work_type — cap enforced
# ---------------------------------------------------------------------------


class TestListByWorkTypeCapped:
    """P12 defensive cap on list_by_work_type."""

    async def test_small_cap_returns_at_most_cap_rows(self, async_session: AsyncSession):
        repo = TodoRepository(async_session)
        await _seed_todos(repo, _SMALL_CAP + 2, work_type="code")
        with patch("general_ludd.db.repository._DEFAULT_LIST_LIMIT", _SMALL_CAP):
            result = await repo.list_by_work_type("code")
        assert len(result) <= _SMALL_CAP

    async def test_explicit_limit_clamped_at_cap(self, async_session: AsyncSession):
        repo = TodoRepository(async_session)
        await _seed_todos(repo, _SMALL_CAP + 2, work_type="code")
        with patch("general_ludd.db.repository._DEFAULT_LIST_LIMIT", _SMALL_CAP):
            result = await repo.list_by_work_type("code", limit=_SMALL_CAP + 50)
        assert len(result) <= _SMALL_CAP


# ---------------------------------------------------------------------------
# TodoRepository.list_due_scheduled — cap enforced
# ---------------------------------------------------------------------------


class TestListDueScheduledCapped:
    """P12 cap on list_due_scheduled (scheduler batch fetch)."""

    async def _seed_scheduled(
        self, repo: TodoRepository, n: int
    ) -> None:
        """Create n todos in SCHEDULED status with a past fire time."""
        past = datetime.now(UTC) - timedelta(hours=1)
        for i in range(n):
            todo = await repo.create({"title": f"sched-{i}"})
            # update() bypasses transition guards so we can set the status directly.
            await repo.update(
                todo.todo_id,
                {
                    "status": "scheduled",
                    "scheduled_at": past,
                    "schedule_paused": False,
                },
                expected_version=1,
            )

    async def test_small_cap_returns_at_most_cap_rows(self, async_session: AsyncSession):
        repo = TodoRepository(async_session)
        await self._seed_scheduled(repo, _SMALL_CAP + 2)
        with patch("general_ludd.db.repository._DEFAULT_LIST_LIMIT", _SMALL_CAP):
            result = await repo.list_due_scheduled(datetime.now(UTC))
        assert len(result) <= _SMALL_CAP

    async def test_explicit_limit_clamped_at_cap(self, async_session: AsyncSession):
        repo = TodoRepository(async_session)
        await self._seed_scheduled(repo, _SMALL_CAP + 2)
        with patch("general_ludd.db.repository._DEFAULT_LIST_LIMIT", _SMALL_CAP):
            result = await repo.list_due_scheduled(
                datetime.now(UTC), limit=_SMALL_CAP + 50
            )
        assert len(result) <= _SMALL_CAP


# ---------------------------------------------------------------------------
# BenchmarkRepository.get_model_scores — defensive cap (dead API path)
# ---------------------------------------------------------------------------


class TestGetModelScoresCapped:
    """P12 defensive cap on get_model_scores."""

    async def _seed_scores(
        self, session: AsyncSession, model_id: str, n: int
    ) -> None:
        for _ in range(n):
            row = BenchmarkResultModel(
                model_profile_id=model_id,
                task_type="code",
            )
            session.add(row)
        await session.flush()

    async def test_small_cap_returns_at_most_cap_rows(self, async_session: AsyncSession):
        await self._seed_scores(async_session, "mdl-test", _SMALL_CAP + 2)
        repo = BenchmarkRepository(session=async_session)
        with patch("general_ludd.db.repository._DEFAULT_LIST_LIMIT", _SMALL_CAP):
            result = await repo.get_model_scores("mdl-test")
        assert len(result) <= _SMALL_CAP


# ---------------------------------------------------------------------------
# VariableNamespaceRepository.load_vars_for_project — defensive cap
# ---------------------------------------------------------------------------


class TestLoadVarsForProjectCapped:
    """P12 defensive cap on load_vars_for_project."""

    async def test_small_cap_returns_at_most_cap_entries(
        self, async_session: AsyncSession
    ):
        """Creating _SMALL_CAP + 2 global vars returns at most _SMALL_CAP entries
        when the cap is patched."""
        ns_repo = VariableNamespaceRepository(async_session)
        for i in range(_SMALL_CAP + 2):
            await ns_repo.set_var("globals", f"key_{i}", f"val_{i}", project_id=None)

        with patch("general_ludd.db.repository._DEFAULT_LIST_LIMIT", _SMALL_CAP):
            result = await ns_repo.load_vars_for_project(None)
        assert len(result) <= _SMALL_CAP


# ---------------------------------------------------------------------------
# PromptProfileRepository.list_for_task_type — defensive cap (dead API path)
# ---------------------------------------------------------------------------


class TestListForTaskTypeCapped:
    """P12 defensive cap on list_for_task_type."""

    async def test_small_cap_returns_at_most_cap_rows(self, async_session: AsyncSession):
        """Profiles with empty task_types match any task_type; cap limits results."""
        repo = PromptProfileRepository(async_session)
        for i in range(_SMALL_CAP + 2):
            await repo.upsert(
                {
                    "name": f"prof-cap-{i}",
                    "source": "test",
                    "prompt_text": f"prompt {i}",
                    "task_types": "[]",  # empty = match all
                }
            )

        with patch("general_ludd.db.repository._DEFAULT_LIST_LIMIT", _SMALL_CAP):
            result = await repo.list_for_task_type("code")
        assert len(result) <= _SMALL_CAP


# ---------------------------------------------------------------------------
# /api/status — count-path correctness (Defect 1)
#
# The handler must use status_summary() aggregate queries, NOT list_all(), so
# queue-depth counts are accurate even when todos exceed _DEFAULT_LIST_LIMIT.
# ---------------------------------------------------------------------------


@pytest.fixture
def daemon_app(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    return create_daemon_app(config_dir=str(config_dir))


class TestApiStatusCountPath:
    """Defect-1: /api/status reports aggregate counts, never a capped list length."""

    async def test_todos_total_reflects_true_count_beyond_cap(
        self, daemon_app, async_engine
    ):
        """todos_total must equal the true DB row count even when it exceeds the
        patched _DEFAULT_LIST_LIMIT.  A list_all()-based handler would under-report."""
        daemon_app.state._session_factory = async_sessionmaker(
            async_engine, class_=AsyncSession, expire_on_commit=False
        )

        true_count = _SMALL_CAP + 2  # Exceeds the sentinel cap
        factory = daemon_app.state._session_factory
        async with factory() as session:
            await _seed_todos(TodoRepository(session), true_count, queue="default")
            await session.commit()

        with patch("general_ludd.db.repository._DEFAULT_LIST_LIMIT", _SMALL_CAP):
            async with AsyncClient(
                transport=ASGITransport(app=daemon_app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["todos_total"] == true_count, (
            f"Expected todos_total={true_count} (aggregate), "
            f"got {data['todos_total']} — handler may be using list_all() instead of "
            "status_summary(), causing silent under-count."
        )

    async def test_queue_depths_accurate_when_rows_exceed_cap(
        self, daemon_app, async_engine
    ):
        """Per-queue depths come from COUNT aggregates, not from a capped list.

        With _SMALL_CAP = 3 and _SMALL_CAP + 1 = 4 items per queue, a
        list_all()-capped response could only see 3 total rows and would
        miscount at least one queue.  status_summary() sees all rows via GROUP BY.
        """
        daemon_app.state._session_factory = async_sessionmaker(
            async_engine, class_=AsyncSession, expire_on_commit=False
        )

        per_queue = _SMALL_CAP + 1  # 4 items each, cap = 3 total
        factory = daemon_app.state._session_factory
        async with factory() as session:
            repo = TodoRepository(session)
            await _seed_todos(repo, per_queue, queue="queue-a")
            await _seed_todos(repo, per_queue, queue="queue-b")
            await session.commit()

        with patch("general_ludd.db.repository._DEFAULT_LIST_LIMIT", _SMALL_CAP):
            async with AsyncClient(
                transport=ASGITransport(app=daemon_app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/status")

        data = resp.json()
        depths = data["queue_depths"]
        assert depths.get("queue-a", 0) == per_queue, (
            f"queue-a: expected {per_queue}, got {depths.get('queue-a', 0)} — "
            "status handler may be using capped list_all()"
        )
        assert depths.get("queue-b", 0) == per_queue, (
            f"queue-b: expected {per_queue}, got {depths.get('queue-b', 0)} — "
            "status handler may be using capped list_all()"
        )

    async def test_status_returns_200_with_db_session(
        self, daemon_app, async_engine
    ):
        """Basic sanity: /api/status returns 200 with a real DB-backed session."""
        daemon_app.state._session_factory = async_sessionmaker(
            async_engine, class_=AsyncSession, expire_on_commit=False
        )

        async with AsyncClient(
            transport=ASGITransport(app=daemon_app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/status")

        assert resp.status_code == 200
        data = resp.json()
        assert "todos_total" in data
        assert "queue_depths" in data
        assert data["todos_total"] == 0  # empty DB
        assert data["queue_depths"] == {}
