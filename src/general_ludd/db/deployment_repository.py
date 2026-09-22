"""Atomic database repository for deployment lifecycle state."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.db.models import DeploymentRecordModel
from general_ludd.schemas.deployment import DeploymentRecord


class DeploymentBusyError(RuntimeError):
    """Raised when another worker owns the destructive lifecycle transition."""


class DeploymentIdentityError(RuntimeError):
    """Raised when an unscoped deployment identity matches multiple owners."""


def _as_record(row: DeploymentRecordModel) -> DeploymentRecord:
    return DeploymentRecord(
        project_id=row.project_id,
        instance_id=row.instance_id,
        working_dir=row.working_dir,
        provider=row.provider,
        model_name=row.model_name,
        state=row.state,
        ip_address=row.ip_address,
        endpoint_url=row.endpoint_url,
        created_at=row.created_at,
    )


def _insert_for_dialect(model: type[DeploymentRecordModel], dialect_name: str) -> Any:
    if dialect_name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as postgresql_insert

        return postgresql_insert(model)
    if dialect_name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        return sqlite_insert(model)
    raise ValueError(f"Deployment registry does not support SQL dialect {dialect_name!r}")


class DeploymentRegistryRepository:
    """Persist deployments without read-modify-write races between workers."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind deployment operations to one transaction-scoped session."""
        self._session = session

    async def upsert(self, record: DeploymentRecord) -> DeploymentRecord:
        """Insert or update exactly one composite-owned deployment record."""
        now = datetime.now(UTC)
        insert = _insert_for_dialect(DeploymentRecordModel, self._session.get_bind().dialect.name)
        stmt = insert.values(
            project_id=record.project_id,
            instance_id=record.instance_id,
            working_dir=record.working_dir,
            provider=record.provider,
            model_name=record.model_name,
            state=record.state,
            ip_address=record.ip_address,
            endpoint_url=record.endpoint_url,
            destroy_owner=None,
            revision=1,
            created_at=record.created_at,
            updated_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                DeploymentRecordModel.project_id,
                DeploymentRecordModel.provider,
                DeploymentRecordModel.instance_id,
            ],
            set_={
                "working_dir": stmt.excluded.working_dir,
                "provider": stmt.excluded.provider,
                "model_name": stmt.excluded.model_name,
                "state": stmt.excluded.state,
                "ip_address": stmt.excluded.ip_address,
                "endpoint_url": stmt.excluded.endpoint_url,
                "destroy_owner": None,
                "revision": DeploymentRecordModel.revision + 1,
                "updated_at": now,
            },
            where=DeploymentRecordModel.state != "destroying",
        ).returning(DeploymentRecordModel)
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise DeploymentBusyError(
                f"deployment {record.instance_id!r} is destroying; upsert cannot replace its owner"
            )
        # INSERT .. RETURNING can resolve to an already-loaded identity-map
        # instance. Refresh it so callers observe the timestamp and revision
        # written by this atomic upsert rather than stale in-session state.
        await self._session.refresh(row)
        return _as_record(row)

    async def _matching_rows(
        self,
        instance_id: str,
        *,
        project_id: str | None = None,
        provider: str | None = None,
    ) -> list[DeploymentRecordModel]:
        stmt = select(DeploymentRecordModel).where(
            DeploymentRecordModel.instance_id == instance_id
        )
        if project_id is not None:
            stmt = stmt.where(DeploymentRecordModel.project_id == project_id)
        if provider is not None:
            stmt = stmt.where(DeploymentRecordModel.provider == provider)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def _resolve_row(
        self,
        instance_id: str,
        *,
        project_id: str | None = None,
        provider: str | None = None,
    ) -> DeploymentRecordModel | None:
        rows = await self._matching_rows(
            instance_id,
            project_id=project_id,
            provider=provider,
        )
        if len(rows) > 1:
            raise DeploymentIdentityError(
                "ambiguous deployment identity; provide project_id and provider for "
                f"instance_id {instance_id!r}"
            )
        return rows[0] if rows else None

    async def get(
        self,
        instance_id: str,
        *,
        project_id: str | None = None,
        provider: str | None = None,
    ) -> DeploymentRecord | None:
        """Return one scoped deployment or reject an ambiguous identity."""
        row = await self._resolve_row(
            instance_id,
            project_id=project_id,
            provider=provider,
        )
        return _as_record(row) if row is not None else None

    async def list(
        self,
        *,
        project_id: str | None = None,
        provider: str | None = None,
    ) -> list[DeploymentRecord]:
        """List deployments restricted by optional project and provider scope."""
        stmt = select(DeploymentRecordModel)
        if project_id is not None:
            stmt = stmt.where(DeploymentRecordModel.project_id == project_id)
        if provider is not None:
            stmt = stmt.where(DeploymentRecordModel.provider == provider)
        result = await self._session.execute(
            stmt.order_by(DeploymentRecordModel.created_at)
        )
        return [_as_record(row) for row in result.scalars().all()]

    async def claim_for_destroy(
        self,
        instance_id: str,
        *,
        owner: str,
        project_id: str | None = None,
        provider: str | None = None,
    ) -> DeploymentRecord:
        """Atomically claim one exactly resolved deployment for destruction."""
        owner = owner[:128]
        current = await self._resolve_row(
            instance_id,
            project_id=project_id,
            provider=provider,
        )
        if current is None:
            raise KeyError(instance_id)
        stmt = (
            update(DeploymentRecordModel)
            .where(
                DeploymentRecordModel.instance_id == instance_id,
                DeploymentRecordModel.project_id == current.project_id,
                DeploymentRecordModel.provider == current.provider,
                DeploymentRecordModel.state.in_(("running", "destroy_failed")),
            )
            .values(
                state="destroying",
                destroy_owner=owner,
                revision=DeploymentRecordModel.revision + 1,
                updated_at=datetime.now(UTC),
            )
            .returning(DeploymentRecordModel)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is not None:
            return _as_record(row)
        raise DeploymentBusyError(
            f"deployment {instance_id!r} is {current.state}; destroy owned by "
            f"{current.destroy_owner or 'another worker'}"
        )

    async def finish_destroy(
        self,
        instance_id: str,
        *,
        owner: str,
        project_id: str | None = None,
        provider: str | None = None,
    ) -> None:
        """Delete a destroyed record only for its fenced worker and owner."""
        current = await self._resolve_row(
            instance_id,
            project_id=project_id,
            provider=provider,
        )
        if current is None:
            await self._raise_stale_owner(
                instance_id,
                project_id=project_id,
                provider=provider,
            )
            return
        result = await self._session.execute(
            delete(DeploymentRecordModel).where(
                DeploymentRecordModel.instance_id == instance_id,
                DeploymentRecordModel.project_id == current.project_id,
                DeploymentRecordModel.provider == current.provider,
                DeploymentRecordModel.state == "destroying",
                DeploymentRecordModel.destroy_owner == owner[:128],
            )
        )
        if (cast(CursorResult[Any], result).rowcount or 0) != 1:
            await self._raise_stale_owner(
                instance_id,
                project_id=current.project_id,
                provider=current.provider,
            )

    async def release_destroy(
        self,
        instance_id: str,
        *,
        owner: str,
        project_id: str | None = None,
        provider: str | None = None,
    ) -> None:
        """Release a failed destroy claim while preserving retry evidence."""
        current = await self._resolve_row(
            instance_id,
            project_id=project_id,
            provider=provider,
        )
        if current is None:
            await self._raise_stale_owner(
                instance_id,
                project_id=project_id,
                provider=provider,
            )
            return
        result = await self._session.execute(
            update(DeploymentRecordModel)
            .where(
                DeploymentRecordModel.instance_id == instance_id,
                DeploymentRecordModel.project_id == current.project_id,
                DeploymentRecordModel.provider == current.provider,
                DeploymentRecordModel.state == "destroying",
                DeploymentRecordModel.destroy_owner == owner[:128],
            )
            .values(
                state="destroy_failed",
                destroy_owner=None,
                revision=DeploymentRecordModel.revision + 1,
                updated_at=datetime.now(UTC),
            )
        )
        if (cast(CursorResult[Any], result).rowcount or 0) != 1:
            await self._raise_stale_owner(
                instance_id,
                project_id=current.project_id,
                provider=current.provider,
            )

    async def _raise_stale_owner(
        self,
        instance_id: str,
        *,
        project_id: str | None = None,
        provider: str | None = None,
    ) -> None:
        current = await self._resolve_row(
            instance_id,
            project_id=project_id,
            provider=provider,
        )
        owner = current.destroy_owner if current is not None else "deleted"
        raise DeploymentBusyError(
            f"deployment {instance_id!r} destroy is owned by {owner or 'another worker'}"
        )
