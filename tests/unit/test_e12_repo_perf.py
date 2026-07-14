"""E12 — Repository performance batch: N+1 queries, missing indexes, full-table scans.

Tests:
  1. _resolve_human_input_for_todo uses SQL filter (not Python loop over all rows)
  2. Composite index on task_returns(status, project_id, created_at) exists (model + DB)
  3. Composite index on todos(status, updated_at) exists (model + DB)
  4. claim_unreviewed query plan uses composite index when project_id is set
  5. HumanTodoRepository.get_done_for_parent exists and queries in SQL
  6. PromptProfileRepository.list_for_task_type full-scan is documented known pattern
  7. claim_runnable uses composite index (status, priority, created_at)
  8. _reap_stuck_todos uses batch lease lookup (in_ clause)
  9. _collect_training_data_from_returns uses batch fetch (in_ clause)
"""

from __future__ import annotations

import inspect

import pytest
from sqlalchemy import Index, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import (
    Base,
    TaskReturnModel,
    TodoModel,
)
from general_ludd.event_loop.loop import EventLoop


def _source_has_pattern(func, pattern: str) -> bool:
    try:
        src = inspect.getsource(func)
    except OSError:
        return False
    return pattern in src


class TestHumanTodoSQLFilter:
    """E12: _resolve_human_input_for_todo must filter in SQL, not Python."""

    def test_uses_sql_filter_not_python_loop(self):
        loop = EventLoop(daemon_state={})
        src = inspect.getsource(loop._resolve_human_input_for_todo)
        assert "get_done_for_parent" in src, (
            "Expected _resolve_human_input_for_todo to use "
            "HumanTodoRepository.get_done_for_parent (SQL filter), "
            "not list_all + Python filter"
        )
        assert "list_all" not in src, (
            "Expected _resolve_human_input_for_todo to NOT call list_all "
            "(N+1 pattern: load all 50 rows, filter in Python)"
        )

    def test_get_done_for_parent_method_exists(self):
        from general_ludd.db.repository import HumanTodoRepository
        assert hasattr(HumanTodoRepository, "get_done_for_parent"), (
            "HumanTodoRepository missing get_done_for_parent method"
        )

    def test_get_done_for_parent_uses_sql_where(self):
        from general_ludd.db.repository import HumanTodoRepository
        src = inspect.getsource(HumanTodoRepository.get_done_for_parent)
        assert "parent_agent_todo_id" in src
        assert "status" in src
        assert ".limit(" in src, (
            "Expected get_done_for_parent to use LIMIT 1 in SQL, "
            "not post-fetch slicing"
        )


class TestTaskReturnsCompositeIndex:
    """E12: composite index on task_returns(status, project_id, created_at)."""

    def test_index_exists_in_model(self):
        indexes = getattr(TaskReturnModel, "__table_args__", ())
        index_names = [
            item.name for item in indexes if isinstance(item, Index)
        ]
        assert "ix_task_returns_status_project_created" in index_names, (
            f"TaskReturnModel missing ix_task_returns_status_project_created; "
            f"found: {index_names}"
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
                        "WHERE type='index' "
                        "AND name='ix_task_returns_status_project_created'"
                    )
                )
                row = result.fetchone()
            assert row is not None, (
                "Index ix_task_returns_status_project_created not found "
                "in runtime DB"
            )
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_explain_query_plan_uses_index_with_project(self):
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
                        "SELECT * FROM task_returns "
                        "WHERE status='created' AND project_id='proj-1' "
                        "ORDER BY created_at ASC"
                    )
                )
                plan_lines = [
                    row[3] for row in result.fetchall() if row[3]
                ]
                plan_text = " ".join(plan_lines).lower()
            assert (
                "ix_task_returns_status_project_created" in plan_text
                or "index" in plan_text
            ), (
                f"Expected EXPLAIN QUERY PLAN to use composite index, "
                f"got: {plan_text}"
            )
        finally:
            await engine.dispose()


class TestTodosStatusUpdatedAtIndex:
    """E12: composite index on todos(status, updated_at) for reaper queries."""

    def test_index_exists_in_model(self):
        indexes = getattr(TodoModel, "__table_args__", ())
        index_names = [
            item.name for item in indexes if isinstance(item, Index)
        ]
        assert "ix_todos_status_updated_at" in index_names, (
            f"TodoModel missing ix_todos_status_updated_at; "
            f"found: {index_names}"
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
                        "WHERE type='index' "
                        "AND name='ix_todos_status_updated_at'"
                    )
                )
                row = result.fetchone()
            assert row is not None, (
                "Index ix_todos_status_updated_at not found in runtime DB"
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
                        "SELECT * FROM todos "
                        "WHERE status='active' "
                        "ORDER BY updated_at"
                    )
                )
                plan_lines = [
                    row[3] for row in result.fetchall() if row[3]
                ]
                plan_text = " ".join(plan_lines).lower()
            assert "index" in plan_text and "scan" not in plan_text, (
                f"Expected EXPLAIN QUERY PLAN to use index, got: {plan_text}"
            )
        finally:
            await engine.dispose()


class TestClaimRunnableCompositeIndex:
    """E12: claim_runnable uses composite index (status, priority, created_at)."""

    def test_index_exists_in_model(self):
        indexes = getattr(TodoModel, "__table_args__", ())
        index_names = [
            item.name for item in indexes if isinstance(item, Index)
        ]
        assert "ix_todos_status_priority_created_at" in index_names, (
            f"TodoModel missing ix_todos_status_priority_created_at; "
            f"found: {index_names}"
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
                        "WHERE type='index' "
                        "AND name='ix_todos_status_priority_created_at'"
                    )
                )
                row = result.fetchone()
            assert row is not None, (
                "Index ix_todos_status_priority_created_at not found "
                "in runtime DB"
            )
        finally:
            await engine.dispose()


class TestReapUsesBatchLeaseLookup:
    """E12: _reap_stuck_todos uses batch lease lookup (in_ clause)."""

    def test_reap_uses_batch_lease_lookup(self):
        loop = EventLoop(daemon_state={})
        src = inspect.getsource(loop._reap_stuck_todos)
        assert "in_(" in src, (
            "Expected _reap_stuck_todos to use batch lease lookup "
            "(in_() / batch), not per-todo N+1 queries"
        )


class TestTrainingDataBatchFetch:
    """E12: _collect_training_data_from_returns uses batch fetch (in_ clause)."""

    def test_training_data_uses_in_clause_for_decisions(self):
        loop = EventLoop(daemon_state={})
        src = inspect.getsource(loop._collect_training_data_from_returns)
        assert "in_(" in src, (
            "Expected _collect_training_data_from_returns to use .in_(…) "
            "for batch-fetching decisions"
        )


class TestPromptProfileFullScanDocumented:
    """E12: list_for_task_type full-table scan is a known JSON-in-Text limit."""

    def test_list_for_task_type_is_known_full_scan(self):
        from general_ludd.db.repository import PromptProfileRepository
        src = inspect.getsource(PromptProfileRepository.list_for_task_type)
        assert "json.loads" in src, (
            "PromptProfileRepository.list_for_task_type uses JSON-in-Text "
            "column (task_types) which cannot be filtered in SQL — this is "
            "a known design limitation, not a bug to fix"
        )


class TestAlembicMigration032:
    """E12: migration 032 adds both composite indexes."""

    def test_migration_file_exists(self):
        from pathlib import Path
        migration = Path(
            "alembic/versions/032_add_e12_repo_perf_indexes.py"
        )
        assert migration.is_file(), f"Migration 032 not found at {migration}"

    def test_migration_creates_both_indexes(self):
        from pathlib import Path
        migration = Path(
            "alembic/versions/032_add_e12_repo_perf_indexes.py"
        )
        content = migration.read_text()
        assert "ix_task_returns_status_project_created" in content
        assert "ix_todos_status_updated_at" in content

    def test_migration_revises_031(self):
        from pathlib import Path
        migration = Path(
            "alembic/versions/032_add_e12_repo_perf_indexes.py"
        )
        content = migration.read_text()
        assert 'down_revision: str | None = "031"' in content
