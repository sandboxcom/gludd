"""Database fencing contracts for managed self-improvement promotion."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from general_ludd.db.models import Base
from general_ludd.db.promotion_repository import (
    CompletedManagedPromotion,
    ImmutableManagedPromotionError,
    ManagedPromotionBusyError,
    ManagedPromotionIdentity,
    ManagedPromotionLease,
    ManagedSelfImprovePromotionRepository,
    StaleManagedPromotionLeaseError,
)


@pytest_asyncio.fixture
async def session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'promotion.db'}",
        echo=False,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as value:
        yield value
    await engine.dispose()


def _identity(tmp_path: Path) -> ManagedPromotionIdentity:
    return ManagedPromotionIdentity(
        artifact_digest="a" * 64,
        plan_identity_digest="b" * 64,
        attempt_identity_digest="c" * 64,
        todo_id="TODO-PROMOTION",
        project_id="project-promotion",
        return_id="RETURN-PROMOTION",
        repo_root=str(tmp_path),
    )


async def test_active_lease_blocks_duplicate_promoter(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    repository = ManagedSelfImprovePromotionRepository(session)
    now = datetime(2029, 1, 1, tzinfo=UTC)
    first = await repository.acquire(
        _identity(tmp_path),
        owner="worker-one",
        now=now,
        lease_duration=timedelta(minutes=30),
    )
    assert isinstance(first, ManagedPromotionLease)
    assert first.fencing_token == 1

    with pytest.raises(ManagedPromotionBusyError, match="active lease"):
        await repository.acquire(
            _identity(tmp_path),
            owner="worker-two",
            now=now + timedelta(minutes=1),
            lease_duration=timedelta(minutes=30),
        )


async def test_expired_lease_increments_fence_and_exposes_stale_worktree(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    repository = ManagedSelfImprovePromotionRepository(session)
    identity = _identity(tmp_path)
    now = datetime(2029, 1, 1, tzinfo=UTC)
    first = await repository.acquire(
        identity,
        owner="worker-one",
        now=now,
        lease_duration=timedelta(minutes=1),
    )
    assert isinstance(first, ManagedPromotionLease)
    await repository.bind_worktree(
        first,
        "self-improve-promote-aaaaaaaaaaaa-1",
        now=now,
    )

    second = await repository.acquire(
        identity,
        owner="worker-two",
        now=now + timedelta(minutes=2),
        lease_duration=timedelta(minutes=30),
    )

    assert isinstance(second, ManagedPromotionLease)
    assert second.fencing_token == 2
    assert second.stale_worktree_branch == "self-improve-promote-aaaaaaaaaaaa-1"
    with pytest.raises(StaleManagedPromotionLeaseError):
        await repository.bind_worktree(
            first,
            "self-improve-promote-aaaaaaaaaaaa-1",
            now=now + timedelta(minutes=2),
        )


async def test_completion_is_idempotently_returned_on_retry(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    repository = ManagedSelfImprovePromotionRepository(session)
    identity = _identity(tmp_path)
    now = datetime(2029, 1, 1, tzinfo=UTC)
    claim = await repository.acquire(
        identity,
        owner="worker-one",
        now=now,
        lease_duration=timedelta(minutes=30),
    )
    assert isinstance(claim, ManagedPromotionLease)
    completed = await repository.complete(
        claim,
        development_commit="d" * 40,
        marker="Gludd-Self-Improve-Artifact=" + "a" * 64,
        now=now + timedelta(minutes=1),
    )

    retry = await repository.acquire(
        identity,
        owner="worker-two",
        now=now + timedelta(minutes=2),
        lease_duration=timedelta(minutes=30),
    )

    assert isinstance(completed, CompletedManagedPromotion)
    assert retry == completed


async def test_artifact_digest_cannot_be_rebound_to_another_todo(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    repository = ManagedSelfImprovePromotionRepository(session)
    identity = _identity(tmp_path)
    now = datetime(2029, 1, 1, tzinfo=UTC)
    claim = await repository.acquire(
        identity,
        owner="worker-one",
        now=now,
        lease_duration=timedelta(minutes=1),
    )
    assert isinstance(claim, ManagedPromotionLease)

    with pytest.raises(ImmutableManagedPromotionError, match="todo_id"):
        await repository.acquire(
            replace(identity, todo_id="TODO-OTHER"),
            owner="worker-two",
            now=now + timedelta(minutes=2),
            lease_duration=timedelta(minutes=1),
        )


async def test_sqlite_restart_preserves_active_lease_time_zone(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    now = datetime(2029, 1, 1, tzinfo=UTC)
    repository = ManagedSelfImprovePromotionRepository(session)
    await repository.acquire(
        _identity(tmp_path),
        owner="worker-one",
        now=now,
        lease_duration=timedelta(minutes=30),
    )
    restarted_factory = async_sessionmaker(session.bind, expire_on_commit=False)

    async with restarted_factory() as restarted:
        with pytest.raises(ManagedPromotionBusyError, match="active lease"):
            await ManagedSelfImprovePromotionRepository(restarted).acquire(
                _identity(tmp_path),
                owner="worker-two",
                now=now + timedelta(minutes=1),
                lease_duration=timedelta(minutes=30),
            )


async def test_sqlite_restart_returns_aware_completed_receipt(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    now = datetime(2029, 1, 1, tzinfo=UTC)
    repository = ManagedSelfImprovePromotionRepository(session)
    claim = await repository.acquire(
        _identity(tmp_path),
        owner="worker-one",
        now=now,
        lease_duration=timedelta(minutes=30),
    )
    assert isinstance(claim, ManagedPromotionLease)
    await repository.complete(
        claim,
        development_commit="d" * 40,
        marker="Gludd-Self-Improve-Artifact=" + "a" * 64,
        now=now + timedelta(minutes=1),
    )
    restarted_factory = async_sessionmaker(session.bind, expire_on_commit=False)

    async with restarted_factory() as restarted:
        completed = await ManagedSelfImprovePromotionRepository(restarted).acquire(
            _identity(tmp_path),
            owner="worker-two",
            now=now + timedelta(minutes=2),
            lease_duration=timedelta(minutes=30),
        )

    assert isinstance(completed, CompletedManagedPromotion)
    assert completed.completed_at.tzinfo is not None
    assert completed.completed_at.utcoffset() is not None


async def test_completion_rejects_marker_not_bound_to_artifact(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    now = datetime(2029, 1, 1, tzinfo=UTC)
    repository = ManagedSelfImprovePromotionRepository(session)
    claim = await repository.acquire(
        _identity(tmp_path),
        owner="worker-one",
        now=now,
        lease_duration=timedelta(minutes=30),
    )
    assert isinstance(claim, ManagedPromotionLease)

    with pytest.raises(ValueError, match=r"marker.*artifact"):
        await repository.complete(
            claim,
            development_commit="d" * 40,
            marker="Gludd-Self-Improve-Artifact=" + "f" * 64,
            now=now + timedelta(minutes=1),
        )


async def test_abandon_releases_current_fence_for_retry(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    now = datetime(2029, 1, 1, tzinfo=UTC)
    repository = ManagedSelfImprovePromotionRepository(session)
    first = await repository.acquire(
        _identity(tmp_path),
        owner="worker-one",
        now=now,
        lease_duration=timedelta(minutes=30),
    )
    assert isinstance(first, ManagedPromotionLease)
    await repository.abandon(first, error="candidate failed", now=now)

    second = await repository.acquire(
        _identity(tmp_path),
        owner="worker-two",
        now=now + timedelta(seconds=1),
        lease_duration=timedelta(minutes=30),
    )

    assert isinstance(second, ManagedPromotionLease)
    assert second.fencing_token == first.fencing_token + 1


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"artifact_digest": "A" * 64}, "64 lowercase"),
        ({"plan_identity_digest": "short"}, "64 lowercase"),
        ({"attempt_identity_digest": object()}, "64 lowercase"),
        ({"todo_id": ""}, "non-empty text"),
        ({"project_id": "project\x00unsafe"}, "safe bound"),
        ({"return_id": "r" * 65}, "safe bound"),
        ({"repo_root": " "}, "non-empty text"),
    ],
)
def test_identity_rejects_malformed_durable_authority(
    tmp_path: Path,
    changes: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "artifact_digest": "a" * 64,
        "plan_identity_digest": "b" * 64,
        "attempt_identity_digest": "c" * 64,
        "todo_id": "TODO-PROMOTION",
        "project_id": "project-promotion",
        "return_id": "RETURN-PROMOTION",
        "repo_root": str(tmp_path),
    }
    values.update(changes)

    with pytest.raises(ValueError, match=message):
        ManagedPromotionIdentity(**cast(Any, values))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"artifact_digest": "invalid"}, "64 lowercase"),
        ({"owner": ""}, "non-empty text"),
        ({"fencing_token": True}, "positive integer"),
        ({"fencing_token": 0}, "positive integer"),
        ({"expires_at": datetime(2029, 1, 1)}, "timezone-aware"),
        ({"stale_worktree_branch": ""}, "non-empty text"),
    ],
)
def test_lease_rejects_malformed_process_boundary_values(
    changes: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "artifact_digest": "a" * 64,
        "owner": "worker-one",
        "fencing_token": 1,
        "expires_at": datetime(2029, 1, 1, tzinfo=UTC),
        "stale_worktree_branch": None,
    }
    values.update(changes)

    with pytest.raises(ValueError, match=message):
        ManagedPromotionLease(**cast(Any, values))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"identity": object()}, "ManagedPromotionIdentity"),
        ({"development_commit": "short"}, "40-character"),
        ({"marker": "wrong"}, "does not match"),
        ({"fencing_token": True}, "positive integer"),
        ({"fencing_token": 0}, "positive integer"),
        ({"completed_at": datetime(2029, 1, 1)}, "timezone-aware"),
    ],
)
def test_completed_receipt_rejects_malformed_persisted_values(
    tmp_path: Path,
    changes: dict[str, object],
    message: str,
) -> None:
    identity = _identity(tmp_path)
    values: dict[str, object] = {
        "identity": identity,
        "development_commit": "d" * 40,
        "marker": "Gludd-Self-Improve-Artifact=" + identity.artifact_digest,
        "fencing_token": 1,
        "completed_at": datetime(2029, 1, 1, tzinfo=UTC),
    }
    values.update(changes)

    with pytest.raises(ValueError, match=message):
        CompletedManagedPromotion(**cast(Any, values))


async def test_repository_rejects_invalid_claim_inputs_before_git_side_effects(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    repository = ManagedSelfImprovePromotionRepository(session)
    now = datetime(2029, 1, 1, tzinfo=UTC)

    with pytest.raises(ValueError, match="ManagedPromotionIdentity"):
        await repository.acquire(
            cast(Any, object()),
            owner="worker-one",
            now=now,
            lease_duration=timedelta(minutes=1),
        )
    with pytest.raises(ValueError, match="lease_duration"):
        await repository.acquire(
            _identity(tmp_path),
            owner="worker-one",
            now=now,
            lease_duration=timedelta(0),
        )

    claim = await repository.acquire(
        _identity(tmp_path),
        owner="worker-one",
        now=now,
        lease_duration=timedelta(minutes=1),
    )
    assert isinstance(claim, ManagedPromotionLease)
    with pytest.raises(ValueError, match="namespaced"):
        await repository.bind_worktree(claim, "development", now=now)
    with pytest.raises(ValueError, match="40-character"):
        await repository.complete(
            claim,
            development_commit="short",
            marker="Gludd-Self-Improve-Artifact=" + claim.artifact_digest,
            now=now,
        )
    with pytest.raises(ValueError, match="ManagedPromotionLease"):
        await repository.bind_worktree(
            cast(Any, object()),
            "self-improve-promote-aaaaaaaaaaaa-1",
            now=now,
        )


async def test_repository_rejects_naive_time_and_unsupported_database(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    repository = ManagedSelfImprovePromotionRepository(session)
    with pytest.raises(ValueError, match="timezone-aware"):
        await repository.acquire(
            _identity(tmp_path),
            owner="worker-one",
            now=datetime(2029, 1, 1),
            lease_duration=timedelta(minutes=1),
        )

    unsupported = AsyncMock(spec=AsyncSession)
    unsupported.get_bind.return_value.dialect.name = "mysql"
    with pytest.raises(ValueError, match="does not support SQL dialect"):
        await ManagedSelfImprovePromotionRepository(unsupported).acquire(
            _identity(tmp_path),
            owner="worker-one",
            now=datetime(2029, 1, 1, tzinfo=UTC),
            lease_duration=timedelta(minutes=1),
        )


async def test_completion_fails_if_durable_receipt_disappears(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    repository = ManagedSelfImprovePromotionRepository(session)
    now = datetime(2029, 1, 1, tzinfo=UTC)
    claim = await repository.acquire(
        _identity(tmp_path),
        owner="worker-one",
        now=now,
        lease_duration=timedelta(minutes=1),
    )
    assert isinstance(claim, ManagedPromotionLease)

    with (
        patch.object(repository, "_load", new=AsyncMock(return_value=None)),
        pytest.raises(RuntimeError, match="disappeared"),
    ):
        await repository.complete(
            claim,
            development_commit="d" * 40,
            marker="Gludd-Self-Improve-Artifact=" + claim.artifact_digest,
            now=now,
        )


@pytest.mark.parametrize(
    "missing",
    ["state", "development_commit", "marker", "completed_at"],
)
def test_incomplete_database_row_cannot_become_receipt(
    tmp_path: Path,
    missing: str,
) -> None:
    identity = _identity(tmp_path)
    values: dict[str, object] = {
        **ManagedSelfImprovePromotionRepository._identity_values(identity),
        "state": "completed",
        "development_commit": "d" * 40,
        "marker": "Gludd-Self-Improve-Artifact=" + identity.artifact_digest,
        "fencing_token": 1,
        "completed_at": datetime(2029, 1, 1, tzinfo=UTC),
    }
    values[missing] = "pending" if missing == "state" else None

    with pytest.raises(ValueError, match="complete receipt"):
        ManagedSelfImprovePromotionRepository._completed(
            cast(Any, SimpleNamespace(**values))
        )
