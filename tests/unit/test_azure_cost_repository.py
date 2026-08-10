"""Additional coverage for Azure cost repository: input validation, edge cases, filters."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.db.azure_cost_repository import (
    AzureCostLeaseClaim,
    AzureCostReconciliationRepository,
    NonMonotonicAzureCostStateError,
    StaleAzureCostLeaseError,
    _canonical_json,
    _require_aware,
)
from general_ludd.db.models import (
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
_RESOURCE = "/subscriptions/sub-1/resourceGroups/rg-1/providers/Microsoft.App/containerApps/app-1"
_NAIVE = datetime(2026, 8, 1, 16)


def _prediction(pid: str = "pred-a", **overrides: object) -> AzureCostPrediction:
    values: dict[str, object] = {
        "prediction_id": pid,
        "prediction_version": 1,
        "todo_id": "TODO-X",
        "subscription_id": "sub-1",
        "resource_group": "rg-1",
        "resource_ids": (_RESOURCE,),
        "meter_ids": ("gpu-meter",),
        "region": "eastus",
        "sku": "SkuX",
        "workload": "wl-x",
        "predicted_cost_usd": 1.0,
        "conservative_ceiling_usd": 2.0,
        "usage_started_at": _NOW - timedelta(hours=2),
        "usage_ended_at": _NOW - timedelta(hours=1),
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
        "payload": {"resource_id": _RESOURCE.lower()},
    }
    values.update(overrides)
    return AzureActualCostObservation(**values)  # type: ignore[arg-type]


async def _persist_and_claim(
    session: Any,
    prediction: AzureCostPrediction,
    *,
    owner: str = "w",
    now: datetime = _NOW,
) -> AzureCostLeaseClaim:
    repo = AzureCostReconciliationRepository(session)
    await repo.persist_prediction(prediction, not_before=now, now=now)
    return (await repo.claim_due(owner=owner, now=now, lease_duration=timedelta(hours=1), limit=1))[0]


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


# ── _require_aware ──────────────────────────────────────────────────


def test_require_aware_accepts_aware_datetime() -> None:
    _require_aware("x", _NOW)


def test_require_aware_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _require_aware("x", _NAIVE)


def test_require_aware_rejects_none_tzinfo() -> None:
    from datetime import timezone

    dt = datetime(2026, 8, 1, 16, tzinfo=timezone(timedelta(0)))
    _require_aware("x", dt)


# ── _canonical_json ─────────────────────────────────────────────────


def test_canonical_json_sorts_keys() -> None:
    result = _canonical_json({"b": 1, "a": 2})
    assert result == '{"a":2,"b":1}'


def test_canonical_json_handles_nested_mapping() -> None:
    result = _canonical_json({"x": {"c": 3, "b": 2}})
    assert result == '{"x":{"b":2,"c":3}}'


def test_canonical_json_handles_sequence() -> None:
    result = _canonical_json({"items": [3, 1, 2]})
    assert result == '{"items":[3,1,2]}'


def test_canonical_json_serializes_datetime_to_iso() -> None:
    result = _canonical_json({"ts": _NOW})
    assert '"2026-08-01T16:00:00+00:00"' in result


def test_canonical_json_rejects_unsupported_type() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        _canonical_json({"val": object()})


def test_canonical_json_accepts_none_bool_int_float_str() -> None:
    assert _canonical_json({"a": None, "b": True, "c": 42, "d": 3.14, "e": "s"}) is not None


# ── persist_prediction input validation ──────────────────────────────


@pytest.mark.asyncio
async def test_persist_rejects_naive_not_before(db_factory: Any) -> None:
    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        with pytest.raises(ValueError, match="timezone-aware"):
            await repo.persist_prediction(_prediction(), not_before=_NAIVE, now=_NOW)


@pytest.mark.asyncio
async def test_persist_rejects_naive_now(db_factory: Any) -> None:
    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        with pytest.raises(ValueError, match="timezone-aware"):
            await repo.persist_prediction(_prediction(), not_before=_NOW, now=_NAIVE)


@pytest.mark.asyncio
async def test_persist_rejects_not_before_before_usage_ended_at(db_factory: Any) -> None:
    too_early = _NOW - timedelta(hours=3)
    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        with pytest.raises(ValueError, match="not_before"):
            await repo.persist_prediction(_prediction(), not_before=too_early, now=_NOW)


@pytest.mark.asyncio
async def test_persist_cross_session_idempotent(db_factory: Any) -> None:
    prediction = _prediction()
    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        await repo.persist_prediction(prediction, not_before=_NOW, now=_NOW)
        await session.commit()

    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        row2 = await repo.persist_prediction(prediction, not_before=_NOW, now=_NOW)
        await session.commit()
        assert row2.prediction_id == prediction.prediction_id
        count = await session.scalar(select(func.count()).select_from(AzureCostPredictionModel))
        assert count == 1


# ── claim_due input validation ──────────────────────────────────────


@pytest.mark.asyncio
async def test_claim_due_rejects_empty_owner(db_factory: Any) -> None:
    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        with pytest.raises(ValueError, match="owner"):
            await repo.claim_due(owner="  ", now=_NOW, lease_duration=timedelta(minutes=5), limit=1)


@pytest.mark.asyncio
async def test_claim_due_rejects_naive_now(db_factory: Any) -> None:
    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        with pytest.raises(ValueError, match="timezone-aware"):
            await repo.claim_due(owner="w", now=_NAIVE, lease_duration=timedelta(minutes=5), limit=1)


@pytest.mark.asyncio
async def test_claim_due_rejects_zero_lease_duration(db_factory: Any) -> None:
    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        with pytest.raises(ValueError, match="lease_duration"):
            await repo.claim_due(owner="w", now=_NOW, lease_duration=timedelta(0), limit=1)


@pytest.mark.asyncio
async def test_claim_due_rejects_negative_lease_duration(db_factory: Any) -> None:
    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        with pytest.raises(ValueError, match="lease_duration"):
            await repo.claim_due(owner="w", now=_NOW, lease_duration=timedelta(minutes=-1), limit=1)


@pytest.mark.asyncio
async def test_claim_due_rejects_zero_limit(db_factory: Any) -> None:
    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        with pytest.raises(ValueError, match="limit"):
            await repo.claim_due(owner="w", now=_NOW, lease_duration=timedelta(minutes=5), limit=0)


@pytest.mark.asyncio
async def test_claim_due_rejects_negative_limit(db_factory: Any) -> None:
    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        with pytest.raises(ValueError, match="limit"):
            await repo.claim_due(owner="w", now=_NOW, lease_duration=timedelta(minutes=5), limit=-1)


@pytest.mark.asyncio
async def test_claim_due_rejects_bool_limit(db_factory: Any) -> None:
    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        with pytest.raises(ValueError, match="limit"):
            await repo.claim_due(owner="w", now=_NOW, lease_duration=timedelta(minutes=5), limit=True)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_claim_due_rejects_limit_exceeding_max(db_factory: Any) -> None:
    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        with pytest.raises(ValueError, match="limit"):
            await repo.claim_due(owner="w", now=_NOW, lease_duration=timedelta(minutes=5), limit=2000)


# ── claim_due with prediction_id filter ─────────────────────────────


@pytest.mark.asyncio
async def test_claim_due_filters_by_prediction_id(db_factory: Any) -> None:
    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        await repo.persist_prediction(_prediction("pid-a"), not_before=_NOW, now=_NOW)
        await repo.persist_prediction(_prediction("pid-b"), not_before=_NOW, now=_NOW)
        await session.commit()

    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        claims = await repo.claim_due(
            owner="w",
            now=_NOW,
            lease_duration=timedelta(hours=1),
            limit=10,
            prediction_id="pid-a",
        )
        assert len(claims) == 1
        assert claims[0].prediction_id == "pid-a"


# ── claim_due excludes terminal states ──────────────────────────────


@pytest.mark.asyncio
async def test_claim_due_excludes_final_state(db_factory: Any) -> None:
    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        prediction = _prediction("pid-final")
        await repo.persist_prediction(prediction, not_before=_NOW, now=_NOW)
        claim = (await repo.claim_due(owner="w", now=_NOW, lease_duration=timedelta(hours=1), limit=1))[0]
        await repo.advance_state(claim, AzureCostLedgerState.USAGE_PENDING, now=_NOW)
        await repo.advance_state(claim, AzureCostLedgerState.QUERY_DUE, now=_NOW)
        await repo.advance_state(claim, AzureCostLedgerState.PARTIAL, now=_NOW)
        await repo.advance_state(claim, AzureCostLedgerState.PROVISIONAL, now=_NOW)
        await repo.advance_state(claim, AzureCostLedgerState.STABLE, now=_NOW)
        await repo.advance_state(claim, AzureCostLedgerState.FINAL, now=_NOW)
        await session.commit()

    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        claims = await repo.claim_due(owner="w2", now=_NOW, lease_duration=timedelta(hours=1), limit=10)
        assert claims == []


@pytest.mark.asyncio
async def test_claim_due_excludes_adjusted_state(db_factory: Any) -> None:
    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        prediction = _prediction("pid-adj")
        await repo.persist_prediction(prediction, not_before=_NOW, now=_NOW)
        claim = (await repo.claim_due(owner="w", now=_NOW, lease_duration=timedelta(hours=1), limit=1))[0]
        for state in (
            AzureCostLedgerState.USAGE_PENDING,
            AzureCostLedgerState.QUERY_DUE,
            AzureCostLedgerState.PARTIAL,
            AzureCostLedgerState.PROVISIONAL,
            AzureCostLedgerState.STABLE,
            AzureCostLedgerState.FINAL,
        ):
            await repo.advance_state(claim, state, now=_NOW)
        row = await session.get(AzureCostPredictionModel, (prediction.prediction_id, prediction.prediction_version))
        assert row is not None
        row.state = AzureCostLedgerState.ADJUSTED.value
        row.state_rank = 7
        await session.commit()

    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        claims = await repo.claim_due(owner="w2", now=_NOW, lease_duration=timedelta(hours=1), limit=10)
        assert claims == []


# ── claim_due returns multiple results ──────────────────────────────


@pytest.mark.asyncio
async def test_claim_due_returns_multiple(db_factory: Any) -> None:
    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        await repo.persist_prediction(_prediction("p1"), not_before=_NOW, now=_NOW)
        await repo.persist_prediction(_prediction("p2"), not_before=_NOW, now=_NOW)
        await repo.persist_prediction(_prediction("p3"), not_before=_NOW, now=_NOW)
        await session.commit()

    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        claims = await repo.claim_due(owner="w", now=_NOW, lease_duration=timedelta(hours=1), limit=5)
        assert len(claims) == 3
        assert {c.prediction_id for c in claims} == {"p1", "p2", "p3"}


@pytest.mark.asyncio
async def test_claim_due_returns_up_to_limit(db_factory: Any) -> None:
    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        for i in range(5):
            await repo.persist_prediction(_prediction(f"p{i}"), not_before=_NOW, now=_NOW)
        await session.commit()

    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        claims = await repo.claim_due(owner="w", now=_NOW, lease_duration=timedelta(hours=1), limit=2)
        assert len(claims) == 2


# ── claim_due respects not_before ───────────────────────────────────


@pytest.mark.asyncio
async def test_claim_due_skips_not_yet_due(db_factory: Any) -> None:
    future_now = _NOW + timedelta(hours=5)
    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        await repo.persist_prediction(
            _prediction("p-future"),
            not_before=future_now,
            now=_NOW,
        )
        await session.commit()

    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        claims = await repo.claim_due(owner="w", now=_NOW, lease_duration=timedelta(hours=1), limit=10)
        assert claims == []

    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        claims = await repo.claim_due(owner="w", now=future_now, lease_duration=timedelta(hours=1), limit=10)
        assert len(claims) == 1


# ── advance_state non-monotonic guards ──────────────────────────────


@pytest.mark.asyncio
async def test_advance_final_requires_stable(db_factory: Any) -> None:
    async with db_factory() as session:
        claim = await _persist_and_claim(session, _prediction("p-skip"))
        await session.commit()

    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        await repo.advance_state(claim, AzureCostLedgerState.USAGE_PENDING, now=_NOW)
        await repo.advance_state(claim, AzureCostLedgerState.QUERY_DUE, now=_NOW)
        await repo.advance_state(claim, AzureCostLedgerState.PARTIAL, now=_NOW)
        await repo.advance_state(claim, AzureCostLedgerState.PROVISIONAL, now=_NOW)
        # SKIP STABLE → go directly to FINAL
        with pytest.raises(NonMonotonicAzureCostStateError, match="STABLE"):
            await repo.advance_state(claim, AzureCostLedgerState.FINAL, now=_NOW)


@pytest.mark.asyncio
async def test_advance_adjusted_requires_final(db_factory: Any) -> None:
    async with db_factory() as session:
        claim = await _persist_and_claim(session, _prediction("p-adj-needs-final"))
        await session.commit()

    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        await repo.advance_state(claim, AzureCostLedgerState.USAGE_PENDING, now=_NOW)
        await repo.advance_state(claim, AzureCostLedgerState.QUERY_DUE, now=_NOW)
        await repo.advance_state(claim, AzureCostLedgerState.PARTIAL, now=_NOW)
        await repo.advance_state(claim, AzureCostLedgerState.PROVISIONAL, now=_NOW)
        await repo.advance_state(claim, AzureCostLedgerState.STABLE, now=_NOW)
        # SKIP FINAL → go directly to ADJUSTED
        with pytest.raises(NonMonotonicAzureCostStateError, match="FINAL"):
            await repo.advance_state(claim, AzureCostLedgerState.ADJUSTED, now=_NOW)


@pytest.mark.asyncio
async def test_advance_final_cannot_become_provisional(db_factory: Any) -> None:
    async with db_factory() as session:
        claim = await _persist_and_claim(session, _prediction("p-final-back"))
        repo = AzureCostReconciliationRepository(session)
        for state in (
            AzureCostLedgerState.USAGE_PENDING,
            AzureCostLedgerState.QUERY_DUE,
            AzureCostLedgerState.PARTIAL,
            AzureCostLedgerState.PROVISIONAL,
            AzureCostLedgerState.STABLE,
            AzureCostLedgerState.FINAL,
        ):
            await repo.advance_state(claim, state, now=_NOW)
        await session.commit()

    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        with pytest.raises(NonMonotonicAzureCostStateError, match="monotonic"):
            await repo.advance_state(claim, AzureCostLedgerState.PROVISIONAL, now=_NOW)


@pytest.mark.asyncio
async def test_advance_adjusted_cannot_become_final(db_factory: Any) -> None:
    async with db_factory() as session:
        claim = await _persist_and_claim(session, _prediction("p-adj-back"))
        repo = AzureCostReconciliationRepository(session)
        for state in (
            AzureCostLedgerState.USAGE_PENDING,
            AzureCostLedgerState.QUERY_DUE,
            AzureCostLedgerState.PARTIAL,
            AzureCostLedgerState.PROVISIONAL,
            AzureCostLedgerState.STABLE,
            AzureCostLedgerState.FINAL,
        ):
            await repo.advance_state(claim, state, now=_NOW)
        row = await session.get(
            AzureCostPredictionModel,
            (claim.prediction_id, claim.prediction_version),
        )
        assert row is not None
        row.state = AzureCostLedgerState.ADJUSTED.value
        row.state_rank = 7
        await session.commit()

    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        with pytest.raises(NonMonotonicAzureCostStateError, match="monotonic"):
            await repo.advance_state(claim, AzureCostLedgerState.FINAL, now=_NOW)


# ── advance_state with event_detail ─────────────────────────────────


@pytest.mark.asyncio
async def test_advance_state_stores_event_detail(db_factory: Any) -> None:
    async with db_factory() as session:
        claim = await _persist_and_claim(session, _prediction("p-detail"))
        await session.commit()

    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        detail = {"reason": "manual review", "auditor": "alice"}
        await repo.advance_state(
            claim,
            AzureCostLedgerState.USAGE_PENDING,
            now=_NOW,
            event_detail=detail,
        )
        await session.commit()

    async with db_factory() as session:
        events = (
            (
                await session.execute(
                    select(AzureCostOutboxEventModel).where(
                        AzureCostOutboxEventModel.event_type == "COST_RECONCILIATION_USAGE_PENDING"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
        assert '"manual review"' in events[0].payload


# ── upsert_actual_cost with stale claim ─────────────────────────────


@pytest.mark.asyncio
async def test_upsert_actual_cost_rejects_stale_claim(db_factory: Any) -> None:
    async with db_factory() as session:
        claim = await _persist_and_claim(session, _prediction("p-stale"))
        await session.commit()

    stale_claim = AzureCostLeaseClaim(
        prediction_id=claim.prediction_id,
        prediction_version=claim.prediction_version,
        owner=claim.owner,
        fencing_token=0,
        expires_at=_NOW + timedelta(hours=1),
    )

    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        with pytest.raises(StaleAzureCostLeaseError, match="stale"):
            await repo.upsert_actual_cost(stale_claim, _observation(), now=_NOW)


# ── upsert_actual_cost sets finalized_at on FINAL ───────────────────


@pytest.mark.asyncio
async def test_final_state_sets_finalized_at(db_factory: Any) -> None:
    async with db_factory() as session:
        claim = await _persist_and_claim(session, _prediction("p-fin-at"))
        repo = AzureCostReconciliationRepository(session)
        for state in (
            AzureCostLedgerState.USAGE_PENDING,
            AzureCostLedgerState.QUERY_DUE,
            AzureCostLedgerState.PARTIAL,
            AzureCostLedgerState.PROVISIONAL,
            AzureCostLedgerState.STABLE,
        ):
            await repo.advance_state(claim, state, now=_NOW)
        await session.commit()

    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        row = await repo.advance_state(claim, AzureCostLedgerState.FINAL, now=_NOW)
        assert row.finalized_at == _NOW

    # re-reading via advance_state (same-state idempotent)
    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        row = await repo.advance_state(claim, AzureCostLedgerState.FINAL, now=_NOW)
        assert row.state == AzureCostLedgerState.FINAL.value


# ── claim_due concurrency — two workers cannot claim same row ───────


@pytest.mark.asyncio
async def test_two_workers_claim_distinct_rows(db_factory: Any) -> None:
    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        await repo.persist_prediction(_prediction("p-a"), not_before=_NOW, now=_NOW)
        await repo.persist_prediction(_prediction("p-b"), not_before=_NOW, now=_NOW)
        await session.commit()

    async with db_factory() as session_a, db_factory() as session_b:
        repo_a = AzureCostReconciliationRepository(session_a)
        repo_b = AzureCostReconciliationRepository(session_b)
        claims_a = await repo_a.claim_due(owner="w-a", now=_NOW, lease_duration=timedelta(hours=1), limit=5)
        claims_b = await repo_b.claim_due(owner="w-b", now=_NOW, lease_duration=timedelta(hours=1), limit=5)
    ids_a = {(c.prediction_id, c.prediction_version) for c in claims_a}
    ids_b = {(c.prediction_id, c.prediction_version) for c in claims_b}
    assert ids_a.isdisjoint(ids_b)
    assert len(ids_a) + len(ids_b) == 2


# ── claim_due re-claiming after release via expiration ──────────────


@pytest.mark.asyncio
async def test_claim_due_returns_none_during_active_lease(db_factory: Any) -> None:
    async with db_factory() as session:
        prediction = _prediction("p-locked")
        await _persist_and_claim(session, prediction, owner="w-a")
        await session.commit()

    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        claims = await repo.claim_due(
            owner="w-b", now=_NOW + timedelta(minutes=1), lease_duration=timedelta(hours=1), limit=10
        )
        assert claims == []


# ── persist_prediction then claim_due with prediction_id mismatch ────


@pytest.mark.asyncio
async def test_claim_due_prediction_id_no_match_returns_empty(db_factory: Any) -> None:
    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        await repo.persist_prediction(_prediction("p-1"), not_before=_NOW, now=_NOW)
        await session.commit()

    async with db_factory() as session:
        repo = AzureCostReconciliationRepository(session)
        claims = await repo.claim_due(
            owner="w",
            now=_NOW,
            lease_duration=timedelta(hours=1),
            limit=10,
            prediction_id="no-such-id",
        )
        assert claims == []
