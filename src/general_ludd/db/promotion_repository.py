"""Durable fencing and receipts for managed self-improvement promotion."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

from sqlalchemy import or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.db.models import ManagedSelfImprovePromotionModel

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_MAX_ERROR_BYTES = 4096


class ManagedPromotionBusyError(RuntimeError):
    """Raised when an unexpired promotion lease belongs to another worker."""


class ImmutableManagedPromotionError(RuntimeError):
    """Raised when an artifact digest is reused with different identities."""


class StaleManagedPromotionLeaseError(RuntimeError):
    """Raised when a promotion write no longer owns the durable fencing token."""


def _require_aware(label: str, value: datetime) -> None:
    """Require one timezone-aware transaction timestamp."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _require_text(label: str, value: str, *, maximum: int) -> str:
    """Return bounded, non-empty, control-safe text."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    normalized = value.strip()
    if "\x00" in normalized or len(normalized.encode("utf-8")) > maximum:
        raise ValueError(f"{label} exceeds its safe bound")
    return normalized


def _require_digest(label: str, value: str) -> str:
    """Return one canonical SHA-256 digest."""
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be 64 lowercase hex characters")
    return value


@dataclass(frozen=True, slots=True)
class ManagedPromotionIdentity:
    """Immutable identity persisted before any external git side effect."""

    artifact_digest: str
    plan_identity_digest: str
    attempt_identity_digest: str
    todo_id: str
    project_id: str
    return_id: str
    repo_root: str

    def __post_init__(self) -> None:
        """Canonicalize and bound every durable identity field."""
        _require_digest("artifact_digest", self.artifact_digest)
        _require_digest("plan_identity_digest", self.plan_identity_digest)
        _require_digest("attempt_identity_digest", self.attempt_identity_digest)
        _require_text("todo_id", self.todo_id, maximum=32)
        _require_text("project_id", self.project_id, maximum=32)
        _require_text("return_id", self.return_id, maximum=64)
        root = _require_text("repo_root", self.repo_root, maximum=1024)
        object.__setattr__(self, "repo_root", str(Path(root).resolve(strict=False)))


@dataclass(frozen=True, slots=True)
class ManagedPromotionLease:
    """Opaque proof of current ownership plus the prior stale worktree."""

    artifact_digest: str
    owner: str
    fencing_token: int
    expires_at: datetime
    stale_worktree_branch: str | None = None

    def __post_init__(self) -> None:
        """Reject malformed lease claims at every process boundary."""
        _require_digest("artifact_digest", self.artifact_digest)
        _require_text("owner", self.owner, maximum=128)
        if isinstance(self.fencing_token, bool) or self.fencing_token <= 0:
            raise ValueError("fencing_token must be a positive integer")
        _require_aware("expires_at", self.expires_at)
        if self.stale_worktree_branch is not None:
            _require_text(
                "stale_worktree_branch",
                self.stale_worktree_branch,
                maximum=128,
            )


@dataclass(frozen=True, slots=True)
class CompletedManagedPromotion:
    """Database-backed record awaiting independent Git marker verification."""

    identity: ManagedPromotionIdentity
    development_commit: str
    marker: str
    fencing_token: int
    completed_at: datetime

    def __post_init__(self) -> None:
        """Validate persisted receipt fields before they leave the repository."""
        if not isinstance(self.identity, ManagedPromotionIdentity):
            raise ValueError("identity must be a ManagedPromotionIdentity")
        if _COMMIT_RE.fullmatch(self.development_commit) is None:
            raise ValueError("development_commit must be a 40-character commit SHA")
        _require_text("marker", self.marker, maximum=256)
        if self.marker != (
            f"Gludd-Self-Improve-Artifact={self.identity.artifact_digest}"
        ):
            raise ValueError("marker does not match the managed artifact")
        if isinstance(self.fencing_token, bool) or self.fencing_token <= 0:
            raise ValueError("fencing_token must be a positive integer")
        _require_aware("completed_at", self.completed_at)


def _dialect_insert(session: AsyncSession) -> Any:
    """Return a conflict-aware insert for the two supported databases."""
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as postgresql_insert

        return postgresql_insert(ManagedSelfImprovePromotionModel)
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        return sqlite_insert(ManagedSelfImprovePromotionModel)
    raise ValueError(f"promotion persistence does not support SQL dialect {dialect!r}")


class ManagedSelfImprovePromotionRepository:
    """Serialize external promotion with committed leases and fencing tokens.

    Unlike ordinary repositories, every mutating method commits its boundary.
    Promotion crosses a database/Git transaction boundary: the claim and
    intended worktree must survive a process crash before Git is touched, and
    the final receipt must become durable before the todo can become COMPLETE.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Bind durable promotion state to a caller-owned session."""
        self._session = session

    async def acquire(
        self,
        identity: ManagedPromotionIdentity,
        *,
        owner: str,
        now: datetime,
        lease_duration: timedelta,
    ) -> ManagedPromotionLease | CompletedManagedPromotion:
        """Insert/claim one identity, or return its immutable completed receipt."""
        if not isinstance(identity, ManagedPromotionIdentity):
            raise ValueError("identity must be a ManagedPromotionIdentity")
        owner = _require_text("owner", owner, maximum=128)
        _require_aware("now", now)
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")

        insert = (
            _dialect_insert(self._session)
            .values(
                **self._identity_values(identity),
                state="pending",
                fencing_token=0,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["artifact_digest"])
        )
        await self._session.execute(insert)
        row = await self._load(identity.artifact_digest)
        if row is None:
            raise RuntimeError("promotion identity insert did not produce a readable row")
        self._verify_immutable_identity(row, identity)
        if row.state == "completed":
            completed = self._completed(row)
            await self._session.commit()
            return completed
        if row.lease_expires_at is not None and row.lease_expires_at > now:
            await self._session.rollback()
            raise ManagedPromotionBusyError(
                f"promotion {identity.artifact_digest} already has an active lease"
            )

        expires_at = now + lease_duration
        previous_token = row.fencing_token
        stale_branch = row.worktree_branch
        guard = (
            update(ManagedSelfImprovePromotionModel)
            .where(
                ManagedSelfImprovePromotionModel.artifact_digest
                == identity.artifact_digest,
                ManagedSelfImprovePromotionModel.fencing_token == previous_token,
                ManagedSelfImprovePromotionModel.state.in_(("pending", "promoting")),
                or_(
                    ManagedSelfImprovePromotionModel.lease_expires_at.is_(None),
                    ManagedSelfImprovePromotionModel.lease_expires_at <= now,
                ),
            )
            .values(
                state="promoting",
                lease_owner=owner,
                lease_expires_at=expires_at,
                fencing_token=previous_token + 1,
                last_error=None,
                updated_at=now,
            )
        )
        result = await self._session.execute(guard)
        if (cast("CursorResult[Any]", result).rowcount or 0) != 1:
            await self._session.rollback()
            raise ManagedPromotionBusyError(
                f"promotion {identity.artifact_digest} lost the claim race"
            )
        await self._session.commit()
        return ManagedPromotionLease(
            artifact_digest=identity.artifact_digest,
            owner=owner,
            fencing_token=previous_token + 1,
            expires_at=expires_at,
            stale_worktree_branch=stale_branch,
        )

    async def bind_worktree(
        self,
        claim: ManagedPromotionLease,
        branch: str,
        *,
        now: datetime,
    ) -> None:
        """Persist the intended branch before creating its external worktree."""
        branch = _require_text("branch", branch, maximum=128)
        if not re.fullmatch(r"self-improve-promote-[0-9a-f]{12}-[1-9][0-9]*", branch):
            raise ValueError("branch is not a namespaced self-improvement branch")
        await self._guarded_update(
            claim,
            now=now,
            values={"worktree_branch": branch, "updated_at": now},
        )
        await self._session.commit()

    async def complete(
        self,
        claim: ManagedPromotionLease,
        *,
        development_commit: str,
        marker: str,
        now: datetime,
    ) -> CompletedManagedPromotion:
        """Fence and persist one independently verified development receipt."""
        if _COMMIT_RE.fullmatch(development_commit) is None:
            raise ValueError("development_commit must be a 40-character commit SHA")
        marker = _require_text("marker", marker, maximum=256)
        if marker != f"Gludd-Self-Improve-Artifact={claim.artifact_digest}":
            raise ValueError("marker does not match the managed artifact")
        await self._guarded_update(
            claim,
            now=now,
            values={
                "state": "completed",
                "lease_owner": None,
                "lease_expires_at": None,
                "worktree_branch": None,
                "development_commit": development_commit,
                "marker": marker,
                "last_error": None,
                "completed_at": now,
                "updated_at": now,
            },
        )
        await self._session.commit()
        row = await self._load(claim.artifact_digest)
        if row is None:
            raise RuntimeError("completed promotion disappeared after commit")
        return self._completed(row)

    async def abandon(
        self,
        claim: ManagedPromotionLease,
        *,
        error: str,
        now: datetime,
    ) -> None:
        """Release a still-current failed claim after its worktree is cleaned."""
        error = error.encode("utf-8")[:_MAX_ERROR_BYTES].decode("utf-8", errors="ignore")
        await self._guarded_update(
            claim,
            now=now,
            require_unexpired=False,
            values={
                "state": "pending",
                "lease_owner": None,
                "lease_expires_at": None,
                "worktree_branch": None,
                "last_error": error,
                "updated_at": now,
            },
        )
        await self._session.commit()

    async def _guarded_update(
        self,
        claim: ManagedPromotionLease,
        *,
        now: datetime,
        values: dict[str, object],
        require_unexpired: bool = True,
    ) -> None:
        """Apply one state mutation only while the exact fence is current."""
        if not isinstance(claim, ManagedPromotionLease):
            raise ValueError("claim must be a ManagedPromotionLease")
        _require_aware("now", now)
        clauses = [
            ManagedSelfImprovePromotionModel.artifact_digest
            == claim.artifact_digest,
            ManagedSelfImprovePromotionModel.state == "promoting",
            ManagedSelfImprovePromotionModel.lease_owner == claim.owner,
            ManagedSelfImprovePromotionModel.fencing_token == claim.fencing_token,
            ManagedSelfImprovePromotionModel.lease_expires_at == claim.expires_at,
        ]
        if require_unexpired:
            clauses.append(ManagedSelfImprovePromotionModel.lease_expires_at > now)
        result = await self._session.execute(
            update(ManagedSelfImprovePromotionModel).where(*clauses).values(**values)
        )
        if (cast("CursorResult[Any]", result).rowcount or 0) != 1:
            await self._session.rollback()
            raise StaleManagedPromotionLeaseError(
                "managed promotion lease is stale, expired, or superseded"
            )

    async def _load(
        self, artifact_digest: str
    ) -> ManagedSelfImprovePromotionModel | None:
        """Load one promotion row by immutable artifact identity."""
        result = await self._session.execute(
            select(ManagedSelfImprovePromotionModel).where(
                ManagedSelfImprovePromotionModel.artifact_digest == artifact_digest
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _identity_values(identity: ManagedPromotionIdentity) -> dict[str, str]:
        """Return the immutable persistence columns for one identity."""
        return {
            "artifact_digest": identity.artifact_digest,
            "plan_identity_digest": identity.plan_identity_digest,
            "attempt_identity_digest": identity.attempt_identity_digest,
            "todo_id": identity.todo_id,
            "project_id": identity.project_id,
            "return_id": identity.return_id,
            "repo_root": identity.repo_root,
        }

    @classmethod
    def _verify_immutable_identity(
        cls,
        row: ManagedSelfImprovePromotionModel,
        identity: ManagedPromotionIdentity,
    ) -> None:
        """Reject reuse of an artifact key for a different authority scope."""
        mismatched = [
            name
            for name, expected in cls._identity_values(identity).items()
            if getattr(row, name) != expected
        ]
        if mismatched:
            raise ImmutableManagedPromotionError(
                "managed promotion identity is immutable; mismatched fields: "
                + ", ".join(sorted(mismatched))
            )

    @staticmethod
    def _completed(
        row: ManagedSelfImprovePromotionModel,
    ) -> CompletedManagedPromotion:
        """Convert a complete row into a strict process-boundary value."""
        if (
            row.state != "completed"
            or row.development_commit is None
            or row.marker is None
            or row.completed_at is None
        ):
            raise ValueError("promotion row does not contain a complete receipt")
        identity = ManagedPromotionIdentity(
            artifact_digest=row.artifact_digest,
            plan_identity_digest=row.plan_identity_digest,
            attempt_identity_digest=row.attempt_identity_digest,
            todo_id=row.todo_id,
            project_id=row.project_id,
            return_id=row.return_id,
            repo_root=row.repo_root,
        )
        return CompletedManagedPromotion(
            identity=identity,
            development_commit=row.development_commit,
            marker=row.marker,
            fencing_token=row.fencing_token,
            completed_at=row.completed_at,
        )
