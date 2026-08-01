"""Durable Azure billed-cost reconciliation repository contracts."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.db.azure_cost_repository import (
    AzureCostReconciliationRepository,
    ImmutableAzureCostIdentityError,
    NonMonotonicAzureCostStateError,
    StaleAzureCostLeaseError,
)
from general_ludd.db.models import (
    AzureCostObservationModel,
    AzureCostOutboxEventModel,
    AzureCostPredictionModel,
    Base,
)
from general_ludd.infra.azure_cost_reconciliation import (
    AzureActualCostObservation,
    AzureCostLedgerState,
    AzureCostPrediction,
)

_NOW = datetime(2026, 8, 1, 16, tzinfo=UTC)
_RESOURCE = (
    "/subscriptions/sub-1/resourceGroups/rg-1/providers/"
    "Microsoft.App/containerApps/app-1"
)


def _prediction(**overrides: object) -> AzureCostPrediction:
    values: dict[str, object] = {
        "prediction_id": "pred-durable-1",
        "prediction_version": 3,
        "todo_id": "TODO-DURABLE-1",
        "subscription_id": "sub-1",
        "resource_group": "rg-1",
        "resource_ids": (_RESOURCE,),
        "meter_ids": ("gpu-meter", "logs-meter"),
        "region": "eastus",
        "sku": "Consumption-GPU-NC8as-T4",
        "workload": "fps-e2e",
        "predicted_cost_usd": 1.5,
        "conservative_ceiling_usd": 2.0,
        "usage_started_at": _NOW - timedelta(hours=2),
        "usage_ended_at": _NOW - timedelta(hours=1),
        "tags": {
            "gludd-reconciliation-id": "recon-1",
            "gludd-work-item-id": "TODO-DURABLE-1",
        },
    }
    values.update(overrides)
    return AzureCostPrediction(**values)  # type: ignore[arg-type]


def _observation(**overrides: object) -> AzureActualCostObservation:
    values: dict[str, object] = {
        "source": "actual-cost-export",
        "snapshot_id": "2026-08-01/run-1/etag-a",
        "row_identity": "line-0001",
        "cost_usd": 1.25,
        "currency": "USD",
        "payload": {
            "resource_id": _RESOURCE.lower(),
            "meter_id": "gpu-meter",
            "usage_date": "2026-08-01",
        },
    }
    values.update(overrides)
    return AzureActualCostObservation(**values)  # type: ignore[arg-type]


@pytest_asyncio.fixture
async def db_factory() -> Any:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_prediction_identity_is_persisted_once_and_cannot_drift(
    db_factory: Any,
) -> None:
    prediction = _prediction()
    async with db_factory() as session:
        repository = AzureCostReconciliationRepository(session)
        first = await repository.persist_prediction(
            prediction,
            not_before=_NOW,
            now=_NOW - timedelta(hours=1),
        )
        duplicate = await repository.persist_prediction(
            prediction,
            not_before=_NOW,
            now=_NOW,
        )
        await session.commit()

        assert duplicate.id == first.id
        assert first.prediction_id == prediction.prediction_id
        assert first.prediction_version == 3
        assert first.state == AzureCostLedgerState.PREDICTED.value
        assert '"resource_ids"' in first.identity_payload
        assert len(first.identity_fingerprint) == 64
        assert (
            await session.scalar(select(func.count()).select_from(AzureCostPredictionModel))
            == 1
        )
        assert (
            await session.scalar(select(func.count()).select_from(AzureCostOutboxEventModel))
            == 1
        )

        drifted = replace(prediction, predicted_cost_usd=1.6)
        with pytest.raises(ImmutableAzureCostIdentityError, match="immutable"):
            await repository.persist_prediction(
                drifted,
                not_before=_NOW,
                now=_NOW,
            )


@pytest.mark.asyncio
async def test_expired_lease_is_recoverable_and_old_fencing_token_is_rejected(
    db_factory: Any,
) -> None:
    prediction = _prediction()
    async with db_factory() as session:
        repository = AzureCostReconciliationRepository(session)
        await repository.persist_prediction(prediction, not_before=_NOW, now=_NOW)
        await session.commit()

    async with db_factory() as session:
        repository = AzureCostReconciliationRepository(session)
        first_claim = (
            await repository.claim_due(
                owner="gunicorn-worker-a",
                now=_NOW,
                lease_duration=timedelta(minutes=5),
                limit=1,
            )
        )[0]
        await session.commit()
    assert first_claim.fencing_token == 1

    async with db_factory() as session:
        repository = AzureCostReconciliationRepository(session)
        assert await repository.claim_due(
            owner="gunicorn-worker-b",
            now=_NOW + timedelta(minutes=4),
            lease_duration=timedelta(minutes=5),
            limit=1,
        ) == []
        await session.rollback()

    takeover_at = _NOW + timedelta(minutes=6)
    async with db_factory() as session:
        repository = AzureCostReconciliationRepository(session)
        second_claim = (
            await repository.claim_due(
                owner="gunicorn-worker-b",
                now=takeover_at,
                lease_duration=timedelta(minutes=5),
                limit=1,
            )
        )[0]
        await session.commit()
    assert second_claim.fencing_token == 2

    async with db_factory() as session:
        repository = AzureCostReconciliationRepository(session)
        with pytest.raises(StaleAzureCostLeaseError, match="stale"):
            await repository.advance_state(
                first_claim,
                AzureCostLedgerState.QUERY_DUE,
                now=takeover_at,
            )
        with pytest.raises(StaleAzureCostLeaseError, match="stale"):
            await repository.upsert_actual_cost(
                first_claim,
                _observation(),
                now=takeover_at,
            )

    async with db_factory() as session:
        repository = AzureCostReconciliationRepository(session)
        row = await repository.advance_state(
            second_claim,
            AzureCostLedgerState.QUERY_DUE,
            now=takeover_at,
        )
        await session.commit()
        assert row.state == AzureCostLedgerState.QUERY_DUE.value


@pytest.mark.asyncio
async def test_actual_cost_upsert_is_idempotent_and_append_only(
    db_factory: Any,
) -> None:
    async with db_factory() as session:
        repository = AzureCostReconciliationRepository(session)
        await repository.persist_prediction(_prediction(), not_before=_NOW, now=_NOW)
        claim = (
            await repository.claim_due(
                owner="worker-a",
                now=_NOW,
                lease_duration=timedelta(hours=1),
                limit=1,
            )
        )[0]
        observation = _observation()
        first = await repository.upsert_actual_cost(claim, observation, now=_NOW)
        duplicate = await repository.upsert_actual_cost(claim, observation, now=_NOW)
        await session.commit()

        assert duplicate.id == first.id
        assert first.fencing_token == claim.fencing_token
        assert (
            await session.scalar(select(func.count()).select_from(AzureCostObservationModel))
            == 1
        )

        corrected_in_place = replace(observation, cost_usd=1.3)
        with pytest.raises(ImmutableAzureCostIdentityError, match="snapshot"):
            await repository.upsert_actual_cost(
                claim,
                corrected_in_place,
                now=_NOW,
            )


@pytest.mark.asyncio
async def test_finality_is_monotonic_and_final_event_is_exactly_once(
    db_factory: Any,
) -> None:
    async with db_factory() as session:
        repository = AzureCostReconciliationRepository(session)
        await repository.persist_prediction(_prediction(), not_before=_NOW, now=_NOW)
        claim = (
            await repository.claim_due(
                owner="worker-final",
                now=_NOW,
                lease_duration=timedelta(hours=2),
                limit=1,
            )
        )[0]
        for state in (
            AzureCostLedgerState.USAGE_PENDING,
            AzureCostLedgerState.QUERY_DUE,
            AzureCostLedgerState.PARTIAL,
            AzureCostLedgerState.PROVISIONAL,
            AzureCostLedgerState.STABLE,
            AzureCostLedgerState.FINAL,
        ):
            await repository.advance_state(claim, state, now=_NOW)

        same_final = await repository.advance_state(
            claim,
            AzureCostLedgerState.FINAL,
            now=_NOW,
        )
        assert same_final.state == AzureCostLedgerState.FINAL.value
        with pytest.raises(NonMonotonicAzureCostStateError, match="monotonic"):
            await repository.advance_state(
                claim,
                AzureCostLedgerState.PROVISIONAL,
                now=_NOW,
            )
        await session.commit()

        final_events = (
            await session.execute(
                select(AzureCostOutboxEventModel).where(
                    AzureCostOutboxEventModel.event_type
                    == "COST_RECONCILIATION_FINAL"
                )
            )
        ).scalars().all()
        assert len(final_events) == 1
        assert '"fencing_token":1' in final_events[0].payload


@pytest.mark.asyncio
async def test_state_and_outbox_event_share_the_callers_transaction(
    db_factory: Any,
) -> None:
    async with db_factory() as session:
        repository = AzureCostReconciliationRepository(session)
        await repository.persist_prediction(_prediction(), not_before=_NOW, now=_NOW)
        claim = (
            await repository.claim_due(
                owner="worker-rollback",
                now=_NOW,
                lease_duration=timedelta(hours=1),
                limit=1,
            )
        )[0]
        await session.commit()

    async with db_factory() as session:
        repository = AzureCostReconciliationRepository(session)
        await repository.advance_state(
            claim,
            AzureCostLedgerState.STABLE,
            now=_NOW,
        )
        await session.rollback()

    async with db_factory() as session:
        row = await session.get(
            AzureCostPredictionModel,
            (_prediction().prediction_id, _prediction().prediction_version),
        )
        assert row is not None
        assert row.state == AzureCostLedgerState.PREDICTED.value
        stable_events = await session.scalar(
            select(func.count())
            .select_from(AzureCostOutboxEventModel)
            .where(
                AzureCostOutboxEventModel.event_type
                == "COST_RECONCILIATION_STABLE"
            )
        )
        assert stable_events == 0
