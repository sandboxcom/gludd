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


def _as_record(row: DeploymentRecordModel) -> DeploymentRecord:
    return DeploymentRecord(
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
        self._session = session

    async def upsert(self, record: DeploymentRecord) -> DeploymentRecord:
        now = datetime.now(UTC)
        insert = _insert_for_dialect(DeploymentRecordModel, self._session.get_bind().dialect.name)
        stmt = insert.values(
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
            index_elements=[DeploymentRecordModel.instance_id],
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

    async def get(self, instance_id: str) -> DeploymentRecord | None:
        row = await self._session.get(DeploymentRecordModel, instance_id)
        return _as_record(row) if row is not None else None

    async def list(self) -> list[DeploymentRecord]:
        result = await self._session.execute(
            select(DeploymentRecordModel).order_by(DeploymentRecordModel.created_at)
        )
        return [_as_record(row) for row in result.scalars().all()]

    async def claim_for_destroy(self, instance_id: str, *, owner: str) -> DeploymentRecord:
        owner = owner[:128]
        stmt = (
            update(DeploymentRecordModel)
            .where(
                DeploymentRecordModel.instance_id == instance_id,
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
        current = await self._session.get(DeploymentRecordModel, instance_id)
        if current is None:
            raise KeyError(instance_id)
        raise DeploymentBusyError(
            f"deployment {instance_id!r} is {current.state}; destroy owned by "
            f"{current.destroy_owner or 'another worker'}"
        )

    async def finish_destroy(self, instance_id: str, *, owner: str) -> None:
        result = await self._session.execute(
            delete(DeploymentRecordModel).where(
                DeploymentRecordModel.instance_id == instance_id,
                DeploymentRecordModel.state == "destroying",
                DeploymentRecordModel.destroy_owner == owner[:128],
            )
        )
        if (cast(CursorResult[Any], result).rowcount or 0) != 1:
            await self._raise_stale_owner(instance_id)

    async def release_destroy(self, instance_id: str, *, owner: str) -> None:
        result = await self._session.execute(
            update(DeploymentRecordModel)
            .where(
                DeploymentRecordModel.instance_id == instance_id,
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
            await self._raise_stale_owner(instance_id)

    async def _raise_stale_owner(self, instance_id: str) -> None:
        current = await self._session.get(DeploymentRecordModel, instance_id)
        owner = current.destroy_owner if current is not None else "deleted"
        raise DeploymentBusyError(
            f"deployment {instance_id!r} destroy is owned by {owner or 'another worker'}"
        )
