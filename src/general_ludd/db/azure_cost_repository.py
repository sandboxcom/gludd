"""Durable, fenced persistence for Azure billed-cost reconciliation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from general_ludd.db.models import (
    AzureCostObservationModel,
    AzureCostOutboxEventModel,
    AzureCostPredictionModel,
)
from general_ludd.infra.azure_cost_reconciliation import (
    AZURE_COST_LEDGER_STATE_RANKS,
    AzureActualCostObservation,
    AzureCostLedgerState,
    AzureCostPrediction,
)

_MAX_CLAIM_BATCH = 1000


class ImmutableAzureCostIdentityError(RuntimeError):
    """Raised when a caller tries to rewrite an immutable source identity."""


class StaleAzureCostLeaseError(RuntimeError):
    """Raised when a worker no longer owns the current fencing token."""


class NonMonotonicAzureCostStateError(RuntimeError):
    """Raised when a state change would weaken already-persisted finality."""


@dataclass(frozen=True)
class AzureCostLeaseClaim:
    """Opaque proof that one worker owns a prediction until ``expires_at``."""

    prediction_id: str
    prediction_version: int
    owner: str
    fencing_token: int
    expires_at: datetime


def _require_aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _json_compatible(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _json_compatible(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_compatible(item) for item in value]
    if isinstance(value, datetime):
        _require_aware("serialized datetime", value)
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError(f"Azure cost payload contains unsupported value {type(value).__name__}")


def _canonical_json(value: object) -> str:
    return json.dumps(
        _json_compatible(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _fingerprint(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _prediction_payload(prediction: AzureCostPrediction) -> str:
    return _canonical_json(
        {
            "prediction_id": prediction.prediction_id,
            "prediction_version": prediction.prediction_version,
            "todo_id": prediction.todo_id,
            "subscription_id": prediction.subscription_id,
            "resource_group": prediction.resource_group,
            "resource_ids": prediction.resource_ids,
            "meter_ids": prediction.meter_ids,
            "region": prediction.region,
            "sku": prediction.sku,
            "workload": prediction.workload,
            "predicted_cost_usd": prediction.predicted_cost_usd,
            "conservative_ceiling_usd": prediction.conservative_ceiling_usd,
            "usage_started_at": prediction.usage_started_at,
            "usage_ended_at": prediction.usage_ended_at,
            "tags": prediction.tags,
        }
    )


def _dialect_insert(session: AsyncSession, model: type[Any]) -> Any:
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as postgresql_insert

        return postgresql_insert(model)
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        return sqlite_insert(model)
    raise ValueError(f"Azure cost persistence does not support SQL dialect {dialect!r}")


class AzureCostReconciliationRepository:
    """Persist predictions and serialize reconciliation work across processes.

    Methods flush but never commit. The caller therefore owns the transaction
    that couples a state mutation to its outbox event.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Bind reconciliation persistence to the caller-owned transaction."""
        self._session = session

    async def persist_prediction(
        self,
        prediction: AzureCostPrediction,
        *,
        not_before: datetime,
        now: datetime,
    ) -> AzureCostPredictionModel:
        """Insert one canonical prediction, or return its byte-identical twin."""
        _require_aware("not_before", not_before)
        _require_aware("now", now)
        if not_before < prediction.usage_ended_at:
            raise ValueError("not_before must not precede usage_ended_at")
        identity_payload = _prediction_payload(prediction)
        identity_fingerprint = _fingerprint(identity_payload)
        stmt = (
            _dialect_insert(self._session, AzureCostPredictionModel)
            .values(
                prediction_id=prediction.prediction_id,
                prediction_version=prediction.prediction_version,
                todo_id=prediction.todo_id,
                identity_fingerprint=identity_fingerprint,
                identity_payload=identity_payload,
                state=AzureCostLedgerState.PREDICTED.value,
                state_rank=AZURE_COST_LEDGER_STATE_RANKS[
                    AzureCostLedgerState.PREDICTED
                ],
                not_before=not_before,
                lease_owner=None,
                lease_expires_at=None,
                fencing_token=0,
                state_changed_at=now,
                finalized_at=None,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=["prediction_id", "prediction_version"]
            )
        )
        await self._session.execute(stmt)
        row = await self._load_prediction(
            prediction.prediction_id,
            prediction.prediction_version,
        )
        if row is None:
            raise RuntimeError("prediction insert did not produce a readable row")
        if row.identity_fingerprint != identity_fingerprint:
            raise ImmutableAzureCostIdentityError(
                "prediction identity is immutable for a prediction ID and version"
            )
        await self._ensure_outbox_event(
            row,
            AzureCostLedgerState.PREDICTED,
            previous_state=None,
            fencing_token=0,
            now=now,
        )
        await self._session.flush()
        return row

    async def claim_due(
        self,
        *,
        owner: str,
        now: datetime,
        lease_duration: timedelta,
        limit: int,
        prediction_id: str | None = None,
    ) -> list[AzureCostLeaseClaim]:
        """Atomically claim due rows, incrementing the durable fencing token."""
        if not owner.strip():
            raise ValueError("owner must be a non-empty string")
        _require_aware("now", now)
        if lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be positive")
        if isinstance(limit, bool) or limit <= 0 or limit > _MAX_CLAIM_BATCH:
            raise ValueError(f"limit must be between 1 and {_MAX_CLAIM_BATCH}")
        excluded_states = (
            AzureCostLedgerState.FINAL.value,
            AzureCostLedgerState.ADJUSTED.value,
            AzureCostLedgerState.NEEDS_REVIEW.value,
            AzureCostLedgerState.AUTH_BLOCKED.value,
        )
        eligible_lease = or_(
            AzureCostPredictionModel.lease_expires_at.is_(None),
            AzureCostPredictionModel.lease_expires_at <= now,
        )
        stmt = (
            select(AzureCostPredictionModel)
            .where(
                AzureCostPredictionModel.not_before <= now,
                AzureCostPredictionModel.state.not_in(excluded_states),
                eligible_lease,
            )
            .order_by(
                AzureCostPredictionModel.not_before,
                AzureCostPredictionModel.prediction_id,
                AzureCostPredictionModel.prediction_version,
            )
            .limit(limit)
        )
        if prediction_id is not None:
            stmt = stmt.where(AzureCostPredictionModel.prediction_id == prediction_id)
        if self._session.get_bind().dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)
        candidates = list((await self._session.execute(stmt)).scalars().all())
        expires_at = now + lease_duration
        claims: list[AzureCostLeaseClaim] = []
        for row in candidates:
            previous_token = row.fencing_token
            guard = (
                update(AzureCostPredictionModel)
                .where(
                    AzureCostPredictionModel.prediction_id == row.prediction_id,
                    AzureCostPredictionModel.prediction_version
                    == row.prediction_version,
                    AzureCostPredictionModel.fencing_token == previous_token,
                    or_(
                        AzureCostPredictionModel.lease_expires_at.is_(None),
                        AzureCostPredictionModel.lease_expires_at <= now,
                    ),
                    AzureCostPredictionModel.state.not_in(excluded_states),
                )
                .values(
                    lease_owner=owner,
                    lease_expires_at=expires_at,
                    fencing_token=previous_token + 1,
                    updated_at=now,
                )
            )
            result = await self._session.execute(guard)
            if (cast("CursorResult[Any]", result).rowcount or 0) != 1:
                continue
            row.lease_owner = owner
            row.lease_expires_at = expires_at
            row.fencing_token = previous_token + 1
            row.updated_at = now
            claims.append(
                AzureCostLeaseClaim(
                    prediction_id=row.prediction_id,
                    prediction_version=row.prediction_version,
                    owner=owner,
                    fencing_token=previous_token + 1,
                    expires_at=expires_at,
                )
            )
        await self._session.flush()
        return claims

    async def upsert_actual_cost(
        self,
        claim: AzureCostLeaseClaim,
        observation: AzureActualCostObservation,
        *,
        now: datetime,
    ) -> AzureCostObservationModel:
        """Insert an immutable source row once while holding the current fence."""
        _require_aware("now", now)
        await self._lock_claim(claim, now=now)
        payload = _canonical_json(
            {
                "source": observation.source,
                "snapshot_id": observation.snapshot_id,
                "row_identity": observation.row_identity,
                "cost_usd": observation.cost_usd,
                "currency": observation.currency,
                "payload": observation.payload,
            }
        )
        payload_fingerprint = _fingerprint(payload)
        identity_columns = {
            "prediction_id": claim.prediction_id,
            "prediction_version": claim.prediction_version,
            "source": observation.source,
            "snapshot_id": observation.snapshot_id,
            "row_identity": observation.row_identity,
        }
        stmt = (
            _dialect_insert(self._session, AzureCostObservationModel)
            .values(
                **identity_columns,
                cost_usd=Decimal(str(observation.cost_usd)),
                currency=observation.currency,
                payload_fingerprint=payload_fingerprint,
                payload=payload,
                fencing_token=claim.fencing_token,
                observed_at=now,
                created_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    "prediction_id",
                    "prediction_version",
                    "source",
                    "snapshot_id",
                    "row_identity",
                ]
            )
        )
        await self._session.execute(stmt)
        row = (
            await self._session.execute(
                select(AzureCostObservationModel).where(
                    *(
                        getattr(AzureCostObservationModel, name) == value
                        for name, value in identity_columns.items()
                    )
                )
            )
        ).scalar_one()
        if row.payload_fingerprint != payload_fingerprint:
            raise ImmutableAzureCostIdentityError(
                "an Azure source snapshot row is immutable; corrected data needs a new snapshot identity"
            )
        await self._session.flush()
        return row

    async def advance_state(
        self,
        claim: AzureCostLeaseClaim,
        state: AzureCostLedgerState,
        *,
        now: datetime,
        event_detail: Mapping[str, object] | None = None,
    ) -> AzureCostPredictionModel:
        """Advance finality and append its deduplicated event under one fence."""
        _require_aware("now", now)
        row = await self._lock_claim(claim, now=now)
        current = AzureCostLedgerState(row.state)
        if current is state:
            await self._ensure_outbox_event(
                row,
                state,
                previous_state=current,
                fencing_token=claim.fencing_token,
                now=now,
                detail=event_detail,
            )
            return row
        current_rank = AZURE_COST_LEDGER_STATE_RANKS[current]
        target_rank = AZURE_COST_LEDGER_STATE_RANKS[state]
        if target_rank < current_rank:
            raise NonMonotonicAzureCostStateError(
                f"Azure cost finality is monotonic: {current.value} cannot become {state.value}"
            )
        if state is AzureCostLedgerState.FINAL and current is not AzureCostLedgerState.STABLE:
            raise NonMonotonicAzureCostStateError(
                "Azure cost finality is monotonic: FINAL requires STABLE"
            )
        if state is AzureCostLedgerState.ADJUSTED and current is not AzureCostLedgerState.FINAL:
            raise NonMonotonicAzureCostStateError(
                "Azure cost finality is monotonic: ADJUSTED requires FINAL"
            )
        if current in (AzureCostLedgerState.FINAL, AzureCostLedgerState.ADJUSTED):
            raise NonMonotonicAzureCostStateError(
                f"Azure cost finality is monotonic: {current.value} cannot become {state.value}"
            )
        row.state = state.value
        row.state_rank = target_rank
        row.state_changed_at = now
        row.updated_at = now
        if state is AzureCostLedgerState.FINAL:
            row.finalized_at = now
        await self._ensure_outbox_event(
            row,
            state,
            previous_state=current,
            fencing_token=claim.fencing_token,
            now=now,
            detail=event_detail,
        )
        await self._session.flush()
        return row

    async def _load_prediction(
        self,
        prediction_id: str,
        prediction_version: int,
    ) -> AzureCostPredictionModel | None:
        return (
            await self._session.execute(
                select(AzureCostPredictionModel).where(
                    AzureCostPredictionModel.prediction_id == prediction_id,
                    AzureCostPredictionModel.prediction_version
                    == prediction_version,
                )
            )
        ).scalar_one_or_none()

    async def _lock_claim(
        self,
        claim: AzureCostLeaseClaim,
        *,
        now: datetime,
    ) -> AzureCostPredictionModel:
        _require_aware("claim.expires_at", claim.expires_at)
        if claim.expires_at <= now:
            raise StaleAzureCostLeaseError(
                "Azure cost lease is stale, expired, or superseded by a newer fencing token"
            )
        stmt = select(AzureCostPredictionModel).where(
            AzureCostPredictionModel.prediction_id == claim.prediction_id,
            AzureCostPredictionModel.prediction_version
            == claim.prediction_version,
            AzureCostPredictionModel.lease_owner == claim.owner,
            AzureCostPredictionModel.fencing_token == claim.fencing_token,
            AzureCostPredictionModel.lease_expires_at == claim.expires_at,
            AzureCostPredictionModel.lease_expires_at > now,
        )
        if self._session.get_bind().dialect.name == "postgresql":
            stmt = stmt.with_for_update()
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise StaleAzureCostLeaseError(
                "Azure cost lease is stale, expired, or superseded by a newer fencing token"
            )
        return row

    async def _ensure_outbox_event(
        self,
        row: AzureCostPredictionModel,
        state: AzureCostLedgerState,
        *,
        previous_state: AzureCostLedgerState | None,
        fencing_token: int,
        now: datetime,
        detail: Mapping[str, object] | None = None,
    ) -> None:
        event_type = f"COST_RECONCILIATION_{state.value}"
        deduplication_key = (
            f"{row.prediction_id}:{row.prediction_version}:{event_type}"
        )
        payload = _canonical_json(
            {
                "prediction_id": row.prediction_id,
                "prediction_version": row.prediction_version,
                "state": state.value,
                "previous_state": (
                    previous_state.value if previous_state is not None else None
                ),
                "fencing_token": fencing_token,
                "detail": detail or {},
            }
        )
        stmt = (
            _dialect_insert(self._session, AzureCostOutboxEventModel)
            .values(
                event_id=str(uuid4()),
                prediction_id=row.prediction_id,
                prediction_version=row.prediction_version,
                event_type=event_type,
                deduplication_key=deduplication_key,
                payload=payload,
                created_at=now,
                published_at=None,
            )
            .on_conflict_do_nothing(index_elements=["deduplication_key"])
        )
        await self._session.execute(stmt)


__all__ = [
    "AzureCostLeaseClaim",
    "AzureCostReconciliationRepository",
    "ImmutableAzureCostIdentityError",
    "NonMonotonicAzureCostStateError",
    "StaleAzureCostLeaseError",
]
