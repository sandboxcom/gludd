"""Behavioral coverage for the extracted expired-lease recovery policy."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import general_ludd.event_loop.lease_recovery as subject
from general_ludd.db.models import BucketLeaseModel, TodoModel
from general_ludd.schemas.todo import TodoStatus

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def _lease(**overrides: object) -> BucketLeaseModel:
    values: dict[str, object] = {
        "bucket_key": "project:project-a:queue:core:todo:todo-1",
        "project_id": "project-a",
        "todo_version": 3,
        "termination_confirmed_at": NOW,
        "cancel_requested_at": None,
        "updated_at": None,
        "expires_at": NOW,
        "holder_id": "worker-1",
    }
    values.update(overrides)
    return BucketLeaseModel(**values)


def _todo(**overrides: object) -> TodoModel:
    values: dict[str, object] = {
        "todo_id": "todo-1",
        "project_id": "project-a",
        "status": TodoStatus.ACTIVE.value,
        "version": 3,
        "title": "fixture",
        "queue": "core",
    }
    values.update(overrides)
    return TodoModel(**values)


def _session(*results: object) -> MagicMock:
    session = MagicMock()
    session.execute = AsyncMock(side_effect=results)
    session.delete = AsyncMock()
    session.flush = AsyncMock()
    return session


def _scalar_result(*rows: object) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = list(rows)
    return result


@pytest.mark.parametrize(
    ("lease", "expected"),
    [
        (_lease(bucket_key=42), None),
        (
            _lease(bucket_key="project:project-a:queue:gpu:todo:todo-1"),
            ("project-a", "todo-1", True),
        ),
        (
            _lease(bucket_key="unowned:queue:core:todo:todo-1"),
            (None, "todo-1", True),
        ),
        (_lease(bucket_key="core:todo-1"), ("project-a", "todo-1", False)),
        (_lease(bucket_key="malformed"), None),
    ],
)
def test_lease_scope_parses_owned_unowned_and_legacy_keys(
    lease: BucketLeaseModel,
    expected: subject.LeaseScope | None,
) -> None:
    """Recovery distinguishes exact ownership from ambiguous legacy scope."""
    assert subject._lease_scope(lease) == expected


def test_matching_todo_refuses_ambiguous_legacy_identity() -> None:
    """A legacy key never guesses between duplicate cross-project todo IDs."""
    project_a = _todo(project_id="project-a")
    project_b = _todo(project_id="project-b")

    assert subject._matching_todo([project_a, project_b], "project-b", True) is project_b
    assert subject._matching_todo([project_a], None, False) is project_a
    assert subject._matching_todo([project_a, project_b], None, False) is None


def test_cancellation_request_is_idempotent() -> None:
    """Repeated ticks preserve the timestamp of the original cancellation request."""
    lease = _lease(termination_confirmed_at=None)
    subject._request_cancellation(lease, NOW)
    later = datetime(2026, 9, 27, 12, 5, tzinfo=UTC)
    subject._request_cancellation(lease, later)

    assert lease.cancel_requested_at == NOW
    assert lease.updated_at == NOW


@pytest.mark.asyncio
@pytest.mark.parametrize("confirmed", [False, True])
async def test_malformed_lease_waits_for_termination_before_delete(
    confirmed: bool,
) -> None:
    """Opaque leases fail closed until their former owner is confirmed stopped."""
    lease = _lease(
        bucket_key="malformed",
        termination_confirmed_at=NOW if confirmed else None,
    )
    session = _session()

    reclaimed = await subject._reclaim_one(session, lease, None, {}, NOW)

    assert reclaimed is confirmed
    if confirmed:
        session.delete.assert_awaited_once_with(lease)
    else:
        session.delete.assert_not_awaited()
        assert lease.cancel_requested_at == NOW


@pytest.mark.asyncio
async def test_exact_scope_mismatch_requests_cancellation() -> None:
    """A project mismatch cannot requeue a todo from another owner."""
    lease = _lease(project_id="project-b")
    session = _session()

    assert (
        await subject._reclaim_one(
            session,
            lease,
            ("project-a", "todo-1", True),
            {"todo-1": [_todo()]},
            NOW,
        )
        is False
    )
    assert lease.cancel_requested_at == NOW
    session.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_inactive_or_missing_todo_discards_stale_lease() -> None:
    """A lease with no active work cannot retain execution ownership."""
    lease = _lease()
    session = _session()

    assert (
        await subject._reclaim_one(
            session,
            lease,
            ("project-a", "todo-1", True),
            {"todo-1": [_todo(status=TodoStatus.COMPLETE.value)]},
            NOW,
        )
        is True
    )
    session.delete.assert_awaited_once_with(lease)


@pytest.mark.asyncio
async def test_live_owner_or_changed_version_only_requests_cancellation() -> None:
    """Requeue requires both confirmed termination and the originally leased version."""
    for lease in (
        _lease(termination_confirmed_at=None),
        _lease(todo_version=2),
    ):
        session = _session()
        reclaimed = await subject._reclaim_one(
            session,
            lease,
            ("project-a", "todo-1", True),
            {"todo-1": [_todo()]},
            NOW,
        )
        assert reclaimed is False
        assert lease.cancel_requested_at == NOW
        session.delete.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("rowcount", [0, 1])
async def test_confirmed_attempt_requeues_with_compare_and_set(rowcount: int) -> None:
    """Only one successful ACTIVE/version CAS can reclaim and delete the lease."""
    transition = SimpleNamespace(rowcount=rowcount)
    session = _session(transition)
    lease = _lease()

    reclaimed = await subject._reclaim_one(
        session,
        lease,
        ("project-a", "todo-1", True),
        {"todo-1": [_todo()]},
        NOW,
    )

    assert reclaimed is (rowcount == 1)
    if rowcount == 1:
        session.delete.assert_awaited_once_with(lease)
    else:
        session.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_load_todos_groups_duplicate_business_ids_by_project() -> None:
    """The batched lookup retains every project candidate for later exact matching."""
    project_a = _todo(project_id="project-a")
    project_b = _todo(project_id="project-b")
    session = _session(_scalar_result(project_a, project_b))

    loaded = await subject._load_todos(
        session,
        {
            "a": ("project-a", "todo-1", True),
            "b": ("project-b", "todo-1", True),
        },
    )

    assert loaded == {"todo-1": [project_a, project_b]}


@pytest.mark.asyncio
async def test_reclaim_expired_leases_flushes_confirmed_malformed_rows() -> None:
    """The public operation reclaims eligible rows and flushes once per batch."""
    lease = _lease(bucket_key="malformed")
    session = _session(_scalar_result(lease))

    assert await subject.reclaim_expired_leases(session, now=NOW) == 1
    session.delete.assert_awaited_once_with(lease)
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_reclaim_expired_leases_returns_without_flush_when_empty() -> None:
    """An empty expiry scan is a true no-op."""
    session = _session(_scalar_result())

    assert await subject.reclaim_expired_leases(session, now=NOW) == 0
    session.flush.assert_not_awaited()
