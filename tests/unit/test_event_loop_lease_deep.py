"""Deep behavioral tests for event_loop/lease.py — lease acquisition, reclaim, release."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.db.models import BucketLeaseModel, TodoModel
from general_ludd.event_loop.lease import (
    LeaseBusyError,
    LeaseRenewalStatus,
    acquire_lease,
    acquire_leases_batch,
    confirm_lease_termination,
    reclaim_expired_leases,
    release_lease,
    renew_lease,
    request_lease_cancellation,
)
from general_ludd.schemas.todo import TodoStatus

# ─── helpers ──────────────────────────────────────────────────────────────────


def _mock_session() -> MagicMock:
    s = MagicMock()
    s.execute = AsyncMock()
    s.add = MagicMock()
    s.delete = AsyncMock()
    s.flush = AsyncMock()
    return s


def _mock_scalar_result(*rows: BucketLeaseModel) -> MagicMock:
    result = MagicMock()
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = list(rows)
    result.scalars.return_value = scalars_mock
    return result


# ─── acquire_leases_batch ─────────────────────────────────────────────────────


class TestAcquireLeasesBatch:
    async def test_no_existing_leases_creates_new(self) -> None:
        session = _mock_session()
        session.execute.return_value = _mock_scalar_result()

        results = await acquire_leases_batch(session, ["bucket-a", "bucket-b"], "holder-1", ttl_seconds=60)

        assert len(results) == 2
        assert results[0].bucket_key == "bucket-a"
        assert results[0].holder_id == "holder-1"
        assert results[1].bucket_key == "bucket-b"
        assert results[1].holder_id == "holder-1"
        assert session.add.call_count == 2
        session.flush.assert_awaited_once()

    async def test_existing_lease_refreshes_expiry(self) -> None:
        session = _mock_session()
        now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
        existing = BucketLeaseModel(
            bucket_key="bucket-a",
            holder_id="holder-1",
            todo_version=2,
            expires_at=now + timedelta(seconds=30),
        )
        existing.id = 42
        session.execute.return_value = _mock_scalar_result(existing)

        with patch("general_ludd.event_loop.lease.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.UTC = UTC
            mock_dt.timedelta = timedelta
            results = await acquire_leases_batch(
                session,
                ["bucket-a"],
                "holder-1",
                ttl_seconds=120,
                todo_versions={"bucket-a": 2},
            )

        assert len(results) == 1
        assert results[0] is existing
        assert results[0].expires_at == now + timedelta(seconds=120)
        session.add.assert_not_called()
        session.flush.assert_awaited_once()

    async def test_mixed_existing_and_new(self) -> None:
        session = _mock_session()
        now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
        existing = BucketLeaseModel(
            bucket_key="bucket-a",
            holder_id="holder-1",
            todo_version=2,
            expires_at=now + timedelta(seconds=30),
        )
        existing.id = 1
        session.execute.return_value = _mock_scalar_result(existing)

        with patch("general_ludd.event_loop.lease.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.UTC = UTC
            mock_dt.timedelta = timedelta
            results = await acquire_leases_batch(
                session,
                ["bucket-a", "bucket-b"],
                "holder-1",
                ttl_seconds=60,
                todo_versions={"bucket-a": 2, "bucket-b": 4},
            )

        assert len(results) == 2
        assert results[0] is existing
        assert results[1].bucket_key == "bucket-b"
        session.add.assert_called_once()
        session.flush.assert_awaited_once()

    async def test_preserves_order_of_input_keys(self) -> None:
        session = _mock_session()
        session.execute.return_value = _mock_scalar_result()

        results = await acquire_leases_batch(session, ["z", "a", "m"], "holder-1")

        assert [r.bucket_key for r in results] == ["z", "a", "m"]

    async def test_project_id_set_on_new_and_existing(self) -> None:
        session = _mock_session()
        now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
        existing = BucketLeaseModel(
            bucket_key="bucket-a",
            holder_id="holder-1",
            todo_version=2,
            expires_at=now + timedelta(seconds=30),
            project_id="PROJ-01",
        )
        existing.id = 1
        session.execute.return_value = _mock_scalar_result(existing)

        with patch("general_ludd.event_loop.lease.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.UTC = UTC
            mock_dt.timedelta = timedelta
            results = await acquire_leases_batch(
                session,
                ["bucket-a", "bucket-b"],
                "holder-1",
                project_id="PROJ-01",
                todo_versions={"bucket-a": 2, "bucket-b": 4},
            )

        assert results[0].project_id == "PROJ-01"
        assert results[1].project_id == "PROJ-01"

    async def test_project_id_none_cannot_refresh_project_owned_lease(self) -> None:
        session = _mock_session()
        now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
        existing = BucketLeaseModel(
            bucket_key="bucket-a",
            holder_id="holder-1",
            todo_version=2,
            expires_at=now + timedelta(seconds=30),
            project_id="ORIGINAL",
        )
        existing.id = 1
        session.execute.return_value = _mock_scalar_result(existing)

        with patch("general_ludd.event_loop.lease.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.UTC = UTC
            mock_dt.timedelta = timedelta
            with pytest.raises(LeaseBusyError, match="different project"):
                await acquire_leases_batch(
                    session,
                    ["bucket-a"],
                    "holder-1",
                    project_id=None,
                    todo_versions={"bucket-a": 2},
                )

        assert existing.project_id == "ORIGINAL"

    async def test_same_attempt_cannot_transfer_between_projects(self) -> None:
        session = _mock_session()
        now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
        existing = BucketLeaseModel(
            bucket_key="bucket-a",
            holder_id="holder-1",
            todo_version=2,
            expires_at=now + timedelta(seconds=30),
            project_id="project-a",
        )
        session.execute.return_value = _mock_scalar_result(existing)

        with (
            patch("general_ludd.event_loop.lease.datetime") as mock_dt,
            pytest.raises(LeaseBusyError, match="different project"),
        ):
            mock_dt.now.return_value = now
            mock_dt.UTC = UTC
            await acquire_leases_batch(
                session,
                ["bucket-a"],
                "holder-1",
                project_id="project-b",
                todo_versions={"bucket-a": 2},
            )

        assert existing.project_id == "project-a"
        session.flush.assert_not_awaited()

    async def test_queries_all_owners_for_requested_keys(self) -> None:
        session = _mock_session()
        session.execute.return_value = _mock_scalar_result()

        await acquire_leases_batch(session, ["k1", "k2"], "holder-X", ttl_seconds=300)

        call_args = session.execute.call_args[0][0]
        compiled = str(call_args.compile(compile_kwargs={"literal_binds": True}))
        assert "holder-X" not in compiled
        assert "k1" in compiled and "k2" in compiled

    async def test_unowned_key_rejects_project_owner(self) -> None:
        session = _mock_session()

        with pytest.raises(ValueError, match="unowned bucket"):
            await acquire_leases_batch(
                session,
                ["unowned:queue:core:todo:TODO-1"],
                "holder-1",
                project_id="project-a",
            )

        session.execute.assert_not_awaited()

    async def test_expired_same_holder_cannot_resurrect_attempt(self) -> None:
        session = _mock_session()
        now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
        existing = BucketLeaseModel(
            bucket_key="bucket-a",
            holder_id="holder-1",
            todo_version=2,
            expires_at=now,
        )
        session.execute.return_value = _mock_scalar_result(existing)

        with (
            patch("general_ludd.event_loop.lease.datetime") as mock_dt,
            pytest.raises(LeaseBusyError, match="already owned"),
        ):
            mock_dt.now.return_value = now
            mock_dt.UTC = UTC
            await acquire_leases_batch(
                session,
                ["bucket-a"],
                "holder-1",
                todo_versions={"bucket-a": 2},
            )

    async def test_busy_batch_does_not_stage_earlier_new_bucket(self) -> None:
        session = _mock_session()
        existing = BucketLeaseModel(
            bucket_key="bucket-busy",
            holder_id="other-holder",
            todo_version=4,
            expires_at=datetime.now(UTC) + timedelta(seconds=60),
        )
        session.execute.return_value = _mock_scalar_result(existing)

        with pytest.raises(LeaseBusyError, match="already owned"):
            await acquire_leases_batch(
                session,
                ["bucket-new", "bucket-busy"],
                "holder-1",
                todo_versions={"bucket-new": 2, "bucket-busy": 4},
            )

        session.add.assert_not_called()
        session.flush.assert_not_awaited()

    async def test_partial_version_fence_cannot_refresh_an_older_attempt(self) -> None:
        """Every bucket in a fenced batch must carry an exact todo version."""
        session = _mock_session()

        with pytest.raises(ValueError, match="exactly match"):
            await acquire_leases_batch(
                session,
                ["bucket-a", "bucket-b"],
                "holder-1",
                todo_versions={"bucket-a": 2},
            )

        session.execute.assert_not_awaited()

    @pytest.mark.parametrize("ttl_seconds", [0, -1, 86_401, True, 1.5])
    async def test_rejects_unsafe_ttl(self, ttl_seconds: object) -> None:
        session = _mock_session()

        with pytest.raises(ValueError, match="ttl_seconds"):
            await acquire_leases_batch(  # type: ignore[arg-type]
                session,
                ["bucket-a"],
                "holder-1",
                ttl_seconds=ttl_seconds,
            )

        session.execute.assert_not_awaited()


# ─── acquire_lease ────────────────────────────────────────────────────────────


class TestAcquireLease:
    async def test_delegates_to_batch_and_returns_single(self) -> None:
        session = _mock_session()
        now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
        session.execute.return_value = _mock_scalar_result()

        with patch("general_ludd.event_loop.lease.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.UTC = UTC
            mock_dt.timedelta = timedelta
            result = await acquire_lease(session, "bucket-a", "holder-1", ttl_seconds=90, project_id="P")

        assert isinstance(result, BucketLeaseModel)
        assert result.bucket_key == "bucket-a"
        assert result.holder_id == "holder-1"
        assert result.project_id == "P"


# ─── release_lease ────────────────────────────────────────────────────────────


class TestReleaseLease:
    async def test_deletes_with_holder_id(self) -> None:
        session = _mock_session()
        cursor = MagicMock()
        cursor.rowcount = 1
        session.execute.return_value = cursor

        deleted = await release_lease(session, "bucket-a", "holder-1")

        assert deleted == 1
        session.execute.assert_awaited_once()
        session.flush.assert_awaited_once()
        compiled = str(session.execute.call_args[0][0].compile(compile_kwargs={"literal_binds": True}))
        assert "bucket-a" in compiled
        assert "holder-1" in compiled

    async def test_deletes_without_holder_id(self) -> None:
        session = _mock_session()
        cursor = MagicMock()
        cursor.rowcount = 2
        session.execute.return_value = cursor

        deleted = await release_lease(session, "bucket-a")

        assert deleted == 2
        compiled = str(session.execute.call_args[0][0].compile(compile_kwargs={"literal_binds": True}))
        assert "bucket-a" in compiled
        assert "holder_id" not in compiled.lower()

    async def test_returns_zero_when_no_rows_match(self) -> None:
        session = _mock_session()
        cursor = MagicMock()
        cursor.rowcount = 0
        session.execute.return_value = cursor

        deleted = await release_lease(session, "nonexistent", "holder-1")

        assert deleted == 0


# ─── reclaim_expired_leases ───────────────────────────────────────────────────


class TestReclaimExpiredLeases:
    async def test_no_expired_leases_returns_zero(self) -> None:
        session = _mock_session()
        session.execute.return_value = _mock_scalar_result()

        count = await reclaim_expired_leases(session)

        assert count == 0
        session.delete.assert_not_called()

    async def test_expired_malformed_lease_deletes_only_after_termination(self) -> None:
        session = _mock_session()
        now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
        expired = BucketLeaseModel(
            bucket_key="no-colon-key",
            holder_id="holder-1",
            expires_at=now - timedelta(seconds=600),
            termination_confirmed_at=now,
        )
        expired.id = 1

        class _ScalarFake:
            @staticmethod
            def all():
                return [expired]

        exec_mock = MagicMock()
        exec_mock.scalars.return_value = _ScalarFake()
        session.execute = AsyncMock(return_value=exec_mock)

        with patch("general_ludd.event_loop.lease.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.UTC = UTC
            mock_dt.timedelta = timedelta
            count = await reclaim_expired_leases(session)

        assert count == 1
        session.delete.assert_awaited_once_with(expired)
        session.flush.assert_awaited_once()

    async def test_expired_malformed_lease_is_retained_until_termination(self) -> None:
        session = _mock_session()
        now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
        expired = BucketLeaseModel(
            bucket_key="no-colon-key",
            holder_id="holder-1",
            expires_at=now - timedelta(seconds=600),
        )
        expired.id = 1
        session.execute.return_value = _mock_scalar_result(expired)

        with patch("general_ludd.event_loop.lease.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.UTC = UTC
            count = await reclaim_expired_leases(session)

        assert count == 0
        assert expired.cancel_requested_at == now
        session.delete.assert_not_awaited()

    async def test_expired_active_attempt_requests_cancellation(self) -> None:
        session = _mock_session()
        now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)

        expired = BucketLeaseModel(
            bucket_key="core:TODO-01",
            holder_id="holder-old",
            todo_version=7,
            expires_at=now - timedelta(seconds=600),
        )
        expired.id = 1
        todo = TodoModel(
            todo_id="TODO-01",
            title="still executing",
            queue="core",
            status=TodoStatus.ACTIVE.value,
            version=7,
        )

        class _ExpiredScalar:
            @staticmethod
            def all():
                return [expired]

        class _TodoScalar:
            @staticmethod
            def all():
                return [todo]

        exec_expired = MagicMock()
        exec_expired.scalars.return_value = _ExpiredScalar()
        exec_todo = MagicMock()
        exec_todo.scalars.return_value = _TodoScalar()
        session.execute = AsyncMock(side_effect=[exec_expired, exec_todo])

        with patch("general_ludd.event_loop.lease.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.UTC = UTC
            mock_dt.timedelta = timedelta
            count = await reclaim_expired_leases(session)

        assert count == 0
        session.delete.assert_not_awaited()
        assert expired.cancel_requested_at == now
        # One batched lease query plus one batched todo query.
        assert session.execute.await_count == 2

    async def test_project_scoped_active_attempt_requests_cancellation(self) -> None:
        session = _mock_session()
        now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
        expired = BucketLeaseModel(
            bucket_key="project:project-a:queue:core:todo:TODO-P",
            holder_id="holder-old",
            todo_version=2,
            project_id="project-a",
            expires_at=now - timedelta(seconds=600),
        )
        todo = TodoModel(
            todo_id="TODO-P",
            title="scoped work",
            queue="core",
            status=TodoStatus.ACTIVE.value,
            version=2,
            project_id="project-a",
        )
        session.execute = AsyncMock(
            side_effect=[_mock_scalar_result(expired), _mock_scalar_result(todo)]
        )

        with patch("general_ludd.event_loop.lease.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.UTC = UTC
            count = await reclaim_expired_leases(session)

        assert count == 0
        assert expired.cancel_requested_at == now
        session.delete.assert_not_awaited()

    async def test_unowned_confirmed_attempt_requeues_exact_unowned_todo(self) -> None:
        session = _mock_session()
        now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
        expired = BucketLeaseModel(
            bucket_key="unowned:queue:core:todo:TODO-U",
            holder_id="holder-old",
            todo_version=4,
            expires_at=now - timedelta(seconds=600),
            termination_confirmed_at=now,
        )
        todo = TodoModel(
            todo_id="TODO-U",
            title="unowned work",
            queue="core",
            status=TodoStatus.ACTIVE.value,
            version=4,
        )
        transition_result = MagicMock(rowcount=1)
        session.execute = AsyncMock(
            side_effect=[
                _mock_scalar_result(expired),
                _mock_scalar_result(todo),
                transition_result,
            ]
        )

        with patch("general_ludd.event_loop.lease.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.UTC = UTC
            count = await reclaim_expired_leases(session)

        assert count == 1
        session.delete.assert_awaited_once_with(expired)

    async def test_legacy_ambiguous_todo_id_deletes_only_opaque_lease(self) -> None:
        session = _mock_session()
        now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
        expired = BucketLeaseModel(
            bucket_key="core:TODO-DUP",
            holder_id="holder-old",
            expires_at=now - timedelta(seconds=600),
        )
        todos = [
            TodoModel(
                todo_id="TODO-DUP",
                title="one",
                queue="core",
                status=TodoStatus.ACTIVE.value,
                project_id="project-a",
            ),
            TodoModel(
                todo_id="TODO-DUP",
                title="two",
                queue="core",
                status=TodoStatus.ACTIVE.value,
                project_id="project-b",
            ),
        ]
        todo_result = MagicMock()
        todo_result.scalars.return_value.all.return_value = todos
        session.execute = AsyncMock(
            side_effect=[_mock_scalar_result(expired), todo_result]
        )

        with patch("general_ludd.event_loop.lease.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.UTC = UTC
            count = await reclaim_expired_leases(session)

        assert count == 1
        session.delete.assert_awaited_once_with(expired)

    async def test_non_string_bucket_requests_cancellation_fail_closed(self) -> None:
        session = _mock_session()
        now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
        expired = MagicMock(
            bucket_key=42,
            cancel_requested_at=None,
            termination_confirmed_at=None,
        )
        session.execute.return_value = _mock_scalar_result(expired)

        with patch("general_ludd.event_loop.lease.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.UTC = UTC
            count = await reclaim_expired_leases(session)

        assert count == 0
        assert expired.cancel_requested_at == now
        session.delete.assert_not_awaited()

    async def test_returns_count_of_expired_leases(self) -> None:
        session = _mock_session()
        now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)

        expired1 = BucketLeaseModel(
            bucket_key="core:TODO-01",
            holder_id="h1",
            expires_at=now - timedelta(seconds=600),
        )
        expired1.id = 1
        expired2 = BucketLeaseModel(
            bucket_key="core:TODO-02",
            holder_id="h2",
            expires_at=now - timedelta(seconds=300),
        )
        expired2.id = 2

        class _ExpiredScalar:
            @staticmethod
            def all():
                return [expired1, expired2]

        class _LiveScalar:
            @staticmethod
            def all():
                return []

        exec_expired = MagicMock()
        exec_expired.scalars.return_value = _ExpiredScalar()
        exec_live = MagicMock()
        exec_live.scalars.return_value = _LiveScalar()
        exec_update1 = MagicMock()
        exec_update2 = MagicMock()
        session.execute = AsyncMock(
            side_effect=[
                exec_expired,
                exec_live,
                exec_update1,
                exec_update2,
            ]
        )

        with patch("general_ludd.event_loop.lease.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.UTC = UTC
            mock_dt.timedelta = timedelta
            count = await reclaim_expired_leases(session)

        assert count == 2
        assert session.delete.await_count == 2
        assert session.flush.await_count == 1

    async def test_release_lease_none_holder_id_does_not_filter(self) -> None:
        session = _mock_session()
        cursor = MagicMock()
        cursor.rowcount = 3
        session.execute.return_value = cursor

        deleted = await release_lease(session, "bucket-a", holder_id=None)

        assert deleted == 3
        compiled = str(session.execute.call_args[0][0].compile(compile_kwargs={"literal_binds": True}))
        assert "bucket-a" in compiled
        assert "holder_id" not in compiled.lower()

    async def test_acquire_leases_batch_empty_keys_returns_empty(self) -> None:
        session = _mock_session()
        session.execute.return_value = _mock_scalar_result()

        results = await acquire_leases_batch(session, [], "holder-1")

        assert results == []
        session.execute.assert_not_awaited()
        session.add.assert_not_called()


class TestLeaseHeartbeatAndTermination:
    async def test_exact_owner_can_request_immediate_cancellation(self) -> None:
        session = _mock_session()
        session.execute.return_value = MagicMock(rowcount=1)
        now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)

        with patch("general_ludd.event_loop.lease.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.UTC = UTC
            requested = await request_lease_cancellation(
                session,
                bucket_key="core:TODO-01",
                holder_id="holder-1",
                todo_version=7,
            )

        assert requested is True
        statement = session.execute.await_args.args[0]
        compiled = statement.compile(compile_kwargs={"literal_binds": True})
        sql = str(compiled)
        assert "core:TODO-01" in sql
        assert "holder-1" in sql
        assert "todo_version = 7" in sql
        assert compiled.params == {}
        session.flush.assert_awaited_once()

    async def test_wrong_attempt_cannot_request_cancellation(self) -> None:
        session = _mock_session()
        session.execute.return_value = MagicMock(rowcount=0)

        requested = await request_lease_cancellation(
            session,
            bucket_key="core:TODO-01",
            holder_id="intruder",
            todo_version=7,
        )

        assert requested is False
        session.flush.assert_awaited_once()

    async def test_renew_returns_renewed_after_exact_cas(self) -> None:
        session = _mock_session()
        cursor = MagicMock(rowcount=1)
        session.execute.return_value = cursor

        status = await renew_lease(
            session,
            bucket_key="core:TODO-01",
            holder_id="holder-1",
            todo_version=7,
            ttl_seconds=60,
        )

        assert status is LeaseRenewalStatus.RENEWED
        session.flush.assert_awaited_once()

    async def test_renew_reports_cancel_request_after_failed_cas(self) -> None:
        session = _mock_session()
        cursor = MagicMock(rowcount=0)
        lease = BucketLeaseModel(
            bucket_key="core:TODO-01",
            holder_id="holder-1",
            todo_version=7,
            expires_at=datetime.now(UTC),
            cancel_requested_at=datetime.now(UTC),
        )
        selected = MagicMock()
        selected.scalar_one_or_none.return_value = lease
        session.execute.side_effect = [cursor, selected]

        status = await renew_lease(
            session,
            bucket_key="core:TODO-01",
            holder_id="holder-1",
            todo_version=7,
        )

        assert status is LeaseRenewalStatus.CANCEL_REQUESTED

    async def test_termination_confirmation_requires_cancelled_exact_owner(self) -> None:
        session = _mock_session()
        session.execute.return_value = MagicMock(rowcount=0)

        confirmed = await confirm_lease_termination(
            session,
            bucket_key="core:TODO-01",
            holder_id="intruder",
            todo_version=7,
        )

        assert confirmed is False
        session.flush.assert_awaited_once()
