"""Performance tests for event-loop/repository batch operations (E12).

Verifies that N+1 query patterns have been replaced with batch operations:
- TodoRepository.get_by_ids batch fetches multiple todos in one query
- acquire_leases_batch acquires multiple leases in one round-trip
- reclaim_expired_leases avoids per-lease live-check queries
- Composite indexes exist on task_returns(status,created_at) and bucket_leases(bucket_key,expires_at)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import Index


def test_todo_repository_has_get_by_ids():
    from general_ludd.db.repository import TodoRepository
    assert hasattr(TodoRepository, "get_by_ids"), "TodoRepository missing get_by_ids batch method"


def test_lease_has_batch_acquire():
    from general_ludd.event_loop.lease import acquire_leases_batch
    assert callable(acquire_leases_batch)


@pytest.mark.asyncio
async def test_get_by_ids_batches_queries():
    from general_ludd.db.repository import TodoRepository

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute.return_value = mock_result

    repo = TodoRepository(mock_session)
    await repo.get_by_ids(["id1", "id2", "id3"])

    assert mock_session.execute.call_count == 1, (
        f"Expected 1 query (batched), got {mock_session.execute.call_count}"
    )


@pytest.mark.asyncio
async def test_acquire_leases_batch_single_query():
    from general_ludd.event_loop.lease import acquire_leases_batch

    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute.return_value = mock_result

    keys = ["core:TODO-1", "core:TODO-2", "core:TODO-3"]
    await acquire_leases_batch(mock_session, keys, holder_id="tick-1")

    assert mock_session.execute.call_count == 1, (
        f"Expected 1 execute call (batch SELECT), got {mock_session.execute.call_count}"
    )


@pytest.mark.asyncio
async def test_reclaim_expired_leases_batches_live_check():
    from general_ludd.event_loop.lease import reclaim_expired_leases

    mock_session = AsyncMock()

    class _FakeLease:
        def __init__(self, id, bucket_key, expires_at):
            self.id = id
            self.bucket_key = bucket_key
            self.expires_at = expires_at

    from datetime import UTC, datetime, timedelta
    past = datetime.now(UTC) - timedelta(hours=1)
    expired = [
        _FakeLease(1, "core:TODO-A", past),
        _FakeLease(2, "core:TODO-B", past),
        _FakeLease(3, "core:TODO-C", past),
    ]

    select_results = []

    def _fake_execute(stmt):
        stmt_str = str(stmt)
        if "in_" in stmt_str or "IN" in stmt_str.upper():
            mock2 = MagicMock()
            mock2.scalars.return_value.all.return_value = []
            return mock2
        mock1 = MagicMock()
        mock1.scalars.return_value.all.return_value = expired
        select_results.append(mock1)
        return mock1

    mock_session.execute.side_effect = _fake_execute

    await reclaim_expired_leases(mock_session)

    from sqlalchemy.sql import Select, Update
    select_count = sum(1 for c in mock_session.execute.call_args_list
                       if isinstance(c.args[0], Select))
    update_count = sum(1 for c in mock_session.execute.call_args_list
                       if isinstance(c.args[0], Update))
    assert select_count == 2, (
        f"Expected 2 SELECT (expired + batch live-check), got {select_count} SELECT + {update_count} UPDATE "
        f"across {mock_session.execute.call_count} total calls"
    )


def test_task_return_model_has_composite_index():
    from general_ludd.db.models import TaskReturnModel
    indexes = getattr(TaskReturnModel, "__table_args__", ())
    index_names = []
    for item in indexes:
        if isinstance(item, Index):
            index_names.append(item.name)
    assert "ix_task_returns_status_created" in index_names, (
        f"TaskReturnModel missing ix_task_returns_status_created index; found: {index_names}"
    )


def test_bucket_lease_model_has_composite_index():
    from general_ludd.db.models import BucketLeaseModel
    indexes = getattr(BucketLeaseModel, "__table_args__", ())
    index_names = []
    for item in indexes:
        if isinstance(item, Index):
            index_names.append(item.name)
    assert "ix_bucket_leases_key_expires" in index_names, (
        f"BucketLeaseModel missing ix_bucket_leases_key_expires index; found: {index_names}"
    )


def test_alembic_migration_031_exists():
    from pathlib import Path
    migration = Path("alembic/versions/031_add_task_returns_bucket_leases_e12_indexes.py")
    assert migration.is_file(), f"Migration 031 not found at {migration}"
    content = migration.read_text()
    assert "ix_task_returns_status_created" in content
    assert "ix_bucket_leases_key_expires" in content


@pytest.mark.asyncio
async def test_reconcile_phase_uses_batch_get_by_ids():
    from general_ludd.event_loop.loop import EventLoop

    mock_session = AsyncMock()
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__.return_value = mock_session

    todo_repo_mock = AsyncMock()
    todo_repo_mock.get_by_ids.return_value = {}

    loop = EventLoop(
        session=mock_session,
        todo_repo=todo_repo_mock,
    )

    class _FakeDecision:
        def __init__(self, matched_todo_id, decision, return_id, project_id=None):
            self.matched_todo_id = matched_todo_id
            self.decision = decision
            self.return_id = return_id
            self.project_id = project_id
            self.created_at = None
            self.confidence = 0.0

    decisions = [
        _FakeDecision("id-A", "complete", "R-A"),
        _FakeDecision("id-B", "needs_more_work", "R-B"),
        _FakeDecision("id-C", "complete", "R-C"),
    ]

    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = decisions
    mock_session.execute.return_value = result_mock

    from general_ludd.schemas.todo import TodoStatus
    todo_a = MagicMock()
    todo_a.todo_id = "id-A"
    todo_a.status = TodoStatus.REVIEWING_RETURN.value
    todo_a.version = 1
    todo_a.project_id = None
    todo_repo_mock.get_by_ids.return_value = {"id-A": todo_a}

    todo_repo_mock.transition.side_effect = None

    loop._attempt_completed_push = AsyncMock(return_value=False)

    await loop._phase_reconcile_completed_decisions()

    get_by_ids_calls = todo_repo_mock.get_by_ids.call_args_list
    assert len(get_by_ids_calls) >= 1, "get_by_ids should have been called (batch fetches todos)"
    assert todo_repo_mock.get_by_id.call_count == 0, (
        f"get_by_id was called {todo_repo_mock.get_by_id.call_count} times — should use get_by_ids batch"
    )
