"""Deep behavioral tests for event_loop/lease.py — lease acquisition, reclaim, release."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.db.models import BucketLeaseModel
from general_ludd.event_loop.lease import (
    acquire_lease,
    acquire_leases_batch,
    reclaim_expired_leases,
    release_lease,
)

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
            expires_at=now,
        )
        existing.id = 42
        session.execute.return_value = _mock_scalar_result(existing)

        with patch("general_ludd.event_loop.lease.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.UTC = UTC
            mock_dt.timedelta = timedelta
            results = await acquire_leases_batch(session, ["bucket-a"], "holder-1", ttl_seconds=120)

        assert len(results) == 1
        assert results[0] is existing
        assert results[0].expires_at == now + timedelta(seconds=120)
        session.add.assert_not_called()
        session.flush.assert_awaited_once()

    async def test_mixed_existing_and_new(self) -> None:
        session = _mock_session()
        now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
        existing = BucketLeaseModel(bucket_key="bucket-a", holder_id="holder-1", expires_at=now)
        existing.id = 1
        session.execute.return_value = _mock_scalar_result(existing)

        with patch("general_ludd.event_loop.lease.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.UTC = UTC
            mock_dt.timedelta = timedelta
            results = await acquire_leases_batch(session, ["bucket-a", "bucket-b"], "holder-1", ttl_seconds=60)

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
        existing = BucketLeaseModel(bucket_key="bucket-a", holder_id="holder-1", expires_at=now)
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
            )

        assert results[0].project_id == "PROJ-01"
        assert results[1].project_id == "PROJ-01"

    async def test_project_id_none_leaves_existing_unchanged(self) -> None:
        session = _mock_session()
        now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
        existing = BucketLeaseModel(
            bucket_key="bucket-a",
            holder_id="holder-1",
            expires_at=now,
            project_id="ORIGINAL",
        )
        existing.id = 1
        session.execute.return_value = _mock_scalar_result(existing)

        with patch("general_ludd.event_loop.lease.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.UTC = UTC
            mock_dt.timedelta = timedelta
            results = await acquire_leases_batch(
                session,
                ["bucket-a"],
                "holder-1",
                project_id=None,
            )

        assert results[0].project_id == "ORIGINAL"

    async def test_queries_only_matching_holder_and_keys(self) -> None:
        session = _mock_session()
        session.execute.return_value = _mock_scalar_result()

        await acquire_leases_batch(session, ["k1", "k2"], "holder-X", ttl_seconds=300)

        call_args = session.execute.call_args[0][0]
        compiled = str(call_args.compile(compile_kwargs={"literal_binds": True}))
        assert "holder-X" in compiled
        assert "k1" in compiled and "k2" in compiled


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

    @pytest.mark.skip(reason="Requires patching datetime.now and TodoModel update — integration-level")
    async def test_expired_lease_with_todo_requeue(self) -> None: ...

    async def test_expired_lease_without_colon_in_key_deletes_only(self) -> None:
        session = _mock_session()
        now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)
        expired = BucketLeaseModel(
            bucket_key="no-colon-key",
            holder_id="holder-1",
            expires_at=now - timedelta(seconds=600),
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

    async def test_expired_lease_with_live_sibling_skips_requeue(self) -> None:
        session = _mock_session()
        now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)

        expired = BucketLeaseModel(
            bucket_key="core:TODO-01",
            holder_id="holder-old",
            expires_at=now - timedelta(seconds=600),
        )
        expired.id = 1

        live = BucketLeaseModel(
            bucket_key="core:TODO-01",
            holder_id="holder-new",
            expires_at=now + timedelta(seconds=600),
        )
        live.id = 2

        class _ExpiredScalar:
            @staticmethod
            def all():
                return [expired]

        class _LiveScalar:
            @staticmethod
            def all():
                return [live]

        exec_expired = MagicMock()
        exec_expired.scalars.return_value = _ExpiredScalar()
        exec_live = MagicMock()
        exec_live.scalars.return_value = _LiveScalar()
        session.execute = AsyncMock(side_effect=[exec_expired, exec_live])

        with patch("general_ludd.event_loop.lease.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.UTC = UTC
            mock_dt.timedelta = timedelta
            count = await reclaim_expired_leases(session)

        assert count == 1
        session.delete.assert_awaited_once_with(expired)
        # Only 2 execute calls: expired query + live query — no UPDATE since live exists
        assert session.execute.await_count == 2

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
        session.execute.assert_awaited_once()
        session.add.assert_not_called()
