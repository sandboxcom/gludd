"""E12 — Event-loop/repository performance batch (TDD test file).

Issue mapping:
  1. N+1 queries in _collect_training_data_from_returns (loop.py ~3804) -> batch fetch
  2. claim_runnable missing composite index -> add (status, queue, scheduled) composite
  3. status_summary full-table scans -> indexed counts
  4. _reap_stuck_todos per-lease N+1 -> single join query
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import (
    Base,
    BucketLeaseModel,
    TodoModel,
    TodoStatus,
)
from general_ludd.db.session import create_async_session_factory, ensure_tables
from general_ludd.event_loop.loop import EventLoop

UTC = UTC


def _source_has_pattern(func, pattern: str) -> bool:
    try:
        src = inspect.getsource(func)
    except OSError:
        return False
    return pattern in src


class TestTrainingDataBatchFetch:

    def test_training_data_uses_in_clause_for_decisions(self):
        loop = EventLoop(daemon_state={})
        func = loop._collect_training_data_from_returns
        assert _source_has_pattern(func, ".in_("), (
            "Expected _collect_training_data_from_returns to use .in_(…) "
            "for batch-fetching decisions (not N individual queries)"
        )

    def test_training_data_uses_in_clause_for_todos(self):
        loop = EventLoop(daemon_state={})
        src = inspect.getsource(loop._collect_training_data_from_returns)
        assert "TaskDecisionModel" in src
        assert "in_(" in src, (
            "Expected _collect_training_data_from_returns to batch-fetch "
            "todos with .in_(todo_ids)"
        )


class TestClaimRunnableCompositeIndex:

    def test_index_exists_in_create_all(self):
        todos_indexes = {
            idx.name: [col.name for col in idx.columns]
            for idx in TodoModel.__table_args__
            if idx.name is not None
        }
        assert "ix_todos_status_queue_scheduled" in todos_indexes, (
            f"Expected ix_todos_status_queue_scheduled in TodoModel.__table_args__, "
            f"got {sorted(todos_indexes)}"
        )

    @pytest.mark.asyncio
    async def test_index_present_in_runtime_db(self):
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='index' AND name='ix_todos_status_queue_scheduled'"
                    )
                )
                row = result.fetchone()
            assert row is not None, (
                "Index ix_todos_status_queue_scheduled not found in runtime DB"
            )
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_explain_query_plan_uses_index(self):
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        "EXPLAIN QUERY PLAN "
                        "SELECT * FROM todos WHERE status='queued' AND queue='core' "
                        "ORDER BY scheduled_at"
                    )
                )
                plan_lines = [row[3] for row in result.fetchall() if row[3]]
                plan_text = " ".join(plan_lines).lower()
            assert "index" in plan_text and "scan" not in plan_text, (
                f"Expected EXPLAIN QUERY PLAN to use index, got: {plan_text}"
            )
        finally:
            await engine.dispose()


class TestStatusSummaryAvoidsFullScan:

    def test_work_type_column_has_index(self):
        work_type_col = TodoModel.__table__.columns.get("work_type")
        assert work_type_col is not None
        assert work_type_col.index, (
            "Expected work_type column to have index=True"
        )

    @pytest.mark.asyncio
    async def test_work_type_index_present_in_db(self):
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='index' AND tbl_name='todos' "
                        "AND name LIKE '%work_type%'"
                    )
                )
                rows = result.fetchall()
            assert len(rows) >= 1, "No work_type index found in runtime DB"
        finally:
            await engine.dispose()


class TestReapStuckTodosSingleQuery:

    def test_reap_uses_batch_lease_lookup(self):
        loop = EventLoop(daemon_state={})
        src = inspect.getsource(loop._reap_stuck_todos)
        assert "in_(" in src, (
            "Expected _reap_stuck_todos to use batch lease lookup "
            "(in_() / batch), not per-todo N+1 queries"
        )

    @pytest.mark.asyncio
    async def test_reap_no_live_leases_requeues_todos(self):
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        try:
            await ensure_tables(engine)
            factory = create_async_session_factory(engine)

            loop = EventLoop(session=factory)
            loop._stuck_timeout_minutes = 5

            from general_ludd.db.repository import TodoRepository

            old_time = datetime.now(UTC) - timedelta(hours=1)

            async with factory() as session:
                repo = TodoRepository(session)
                todo = TodoModel(
                    todo_id="T-STUCK-001",
                    title="Stuck test todo",
                    status=TodoStatus.ACTIVE.value,
                    queue="core",
                    updated_at=old_time,
                    version=1,
                )
                session.add(todo)
                await session.commit()

                loop._active_session = session
                loop._todo_repo = repo
                await loop._reap_stuck_todos()

                await session.refresh(todo)
                assert todo.status == TodoStatus.QUEUED.value, (
                    f"Expected QUEUED after reaping, got {todo.status}"
                )
                assert todo.version == 2, (
                    f"Expected version=2, got {todo.version}"
                )
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_reap_preserves_todos_with_live_leases(self):
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        try:
            await ensure_tables(engine)
            factory = create_async_session_factory(engine)

            loop = EventLoop(session=factory)
            loop._stuck_timeout_minutes = 5

            from general_ludd.db.repository import TodoRepository

            old_time = datetime.now(UTC) - timedelta(hours=1)
            future_time = datetime.now(UTC) + timedelta(hours=1)

            async with factory() as session:
                repo = TodoRepository(session)
                todo = TodoModel(
                    todo_id="T-LIVE-001",
                    title="Has live lease",
                    status=TodoStatus.ACTIVE.value,
                    queue="core",
                    updated_at=old_time,
                    version=1,
                )
                session.add(todo)
                await session.flush()

                lease = BucketLeaseModel(
                    bucket_key="core:T-LIVE-001",
                    holder_id="worker-42",
                    expires_at=future_time,
                )
                session.add(lease)
                await session.commit()

                loop._active_session = session
                loop._todo_repo = repo
                await loop._reap_stuck_todos()

                await session.refresh(todo)
                assert todo.status == TodoStatus.ACTIVE.value, (
                    f"Expected ACTIVE (live lease), got {todo.status}"
                )
        finally:
            await engine.dispose()
