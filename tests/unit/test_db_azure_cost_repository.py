"""Deep tests for db/azure_cost_repository.py.

Covers pure helper functions, error types, AzureCostLeaseClaim dataclass,
and the full AzureCostReconciliationRepository surface via sqlite+aiosqlite.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from general_ludd.db.azure_cost_repository import (
    AzureCostLeaseClaim,
    AzureCostReconciliationRepository,
    ImmutableAzureCostIdentityError,
    NonMonotonicAzureCostStateError,
    StaleAzureCostLeaseError,
    _canonical_json,
    _dialect_insert,
    _fingerprint,
    _json_compatible,
    _prediction_payload,
    _require_aware,
)
from general_ludd.db.models import AzureCostPredictionModel, Base
from general_ludd.infra.azure_cost_reconciliation import (
    AzureActualCostObservation,
    AzureCostLedgerState,
    AzureCostPrediction,
)

UTC = UTC

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime(2026, 8, 10, 12, 0, 0, tzinfo=UTC)


def _prediction(
    *,
    prediction_id: str = "pred-001",
    tags: Mapping[str, str] | None = None,
) -> AzureCostPrediction:
    return AzureCostPrediction(
        prediction_id=prediction_id,
        todo_id="todo-001",
        subscription_id="sub-001",
        resource_group="rg-east",
        resource_ids=(
            "/subscriptions/sub-001/resourceGroups/rg-east/providers/Microsoft.Compute/virtualMachines/vm-a",
        ),
        meter_ids=("meter-001",),
        region="eastus",
        sku="Standard_D2s_v3",
        workload="cicd",
        predicted_cost_usd=10.0,
        conservative_ceiling_usd=15.0,
        usage_started_at=datetime(2026, 8, 1, tzinfo=UTC),
        usage_ended_at=datetime(2026, 8, 7, tzinfo=UTC),
        prediction_version=1,
        tags=tags or {},
    )


def _observation() -> AzureActualCostObservation:
    return AzureActualCostObservation(
        source="cost-management",
        snapshot_id="snap-001",
        row_identity="row-001",
        cost_usd=10.0,
        currency="USD",
    )


# ---------------------------------------------------------------------------
# Pure-function tests
# ---------------------------------------------------------------------------


class TestRequireAware:
    def test_accepts_aware_datetime(self) -> None:
        _require_aware("x", datetime(2026, 1, 1, tzinfo=UTC))

    def test_rejects_naive_datetime(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            _require_aware("x", datetime(2026, 1, 1))

    def test_rejects_none_utcoffset(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            _require_aware("x", datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None))


class TestJsonCompatible:
    def test_strings_pass_through(self) -> None:
        assert _json_compatible("hello") == "hello"

    def test_numbers_pass_through(self) -> None:
        assert _json_compatible(42) == 42
        assert _json_compatible(3.14) == 3.14
        assert _json_compatible(True) is True

    def test_none_passes_through(self) -> None:
        assert _json_compatible(None) is None

    def test_list_recurses(self) -> None:
        result: object = _json_compatible([1, {"z": 2, "a": 1}])
        assert result == [1, {"a": 1, "z": 2}]

    def test_tuple_recurses(self) -> None:
        result: object = _json_compatible((1, 2))
        assert result == [1, 2]

    def test_dict_sort_keys(self) -> None:
        result = _json_compatible({"z": 3, "a": 1, "m": 2})
        assert isinstance(result, dict)
        assert list(result) == ["a", "m", "z"]

    def test_nested_dict_sort_keys(self) -> None:
        result: object = _json_compatible({"outer": {"b": 2, "a": 1}})
        assert result == {"outer": {"a": 1, "b": 2}}

    def test_datetime_isoformat(self) -> None:
        dt = datetime(2026, 6, 15, 10, 30, 45, tzinfo=UTC)
        assert _json_compatible(dt) == "2026-06-15T10:30:45+00:00"

    def test_rejects_unsupported_type(self) -> None:
        with pytest.raises(ValueError, match="unsupported"):
            _json_compatible(b"bytes")

    def test_handles_deeply_nested(self) -> None:
        value = {
            "level1": {
                "level2": [{"b": 2, "a": 1}, {"c": 3}],
                "d": True,
            },
            "z": None,
        }
        result: object = _json_compatible(value)
        assert result == {
            "level1": {
                "d": True,
                "level2": [{"a": 1, "b": 2}, {"c": 3}],
            },
            "z": None,
        }


class TestCanonicalJson:
    def test_produces_deterministic_output(self) -> None:
        payload = {"z": 1, "a": 2}
        a = _canonical_json(payload)
        b = _canonical_json(payload)
        assert a == b

    def test_sorted_keys(self) -> None:
        parsed: dict[str, object] = json.loads(_canonical_json({"z": 1, "a": 2}))
        assert list(parsed.keys()) == ["a", "z"]

    def test_no_nan(self) -> None:
        with pytest.raises(ValueError, match=r"NaN|nan") as exc_info:
            _canonical_json({"value": float("nan")})
        assert "nan" in str(exc_info.value).lower()

    def test_ascii_only(self) -> None:
        output = _canonical_json({"key": "val"})
        assert all(ord(c) < 128 for c in output)


class TestFingerprint:
    def test_known_output(self) -> None:
        result = _fingerprint("hello")
        assert len(result) == 64
        assert result == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"

    def test_different_inputs_different_fingerprints(self) -> None:
        a = _fingerprint("a")
        b = _fingerprint("b")
        assert a != b

    def test_same_input_same_fingerprint(self) -> None:
        assert _fingerprint("payload") == _fingerprint("payload")


class TestPredictionPayload:
    def test_returns_json_string(self) -> None:
        pred = _prediction()
        payload = _prediction_payload(pred)
        assert isinstance(payload, str)
        parsed: dict[str, object] = json.loads(payload)
        assert parsed["prediction_id"] == "pred-001"

    def test_identical_predictions_produce_identical_payloads(self) -> None:
        a = _prediction_payload(_prediction())
        b = _prediction_payload(_prediction())
        assert a == b

    def test_encodes_tags(self) -> None:
        pred = _prediction(tags={"env": "prod", "team": "infra"})
        payload = _prediction_payload(pred)
        parsed: dict[str, object] = json.loads(payload)
        assert parsed["tags"] == {"env": "prod", "team": "infra"}

    def test_datetime_fields_are_isoformat(self) -> None:
        pred = _prediction()
        payload = _prediction_payload(pred)
        parsed: dict[str, object] = json.loads(payload)
        assert "+00:00" in str(parsed["usage_started_at"])


class TestDialectInsert:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_sqlite_dialect(self) -> None:
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                result = _dialect_insert(session, AzureCostPredictionModel)
                assert str(type(result).__module__).startswith("sqlalchemy.dialects.sqlite")
        finally:
            await engine.dispose()

    def test_unsupported_dialect_raises(self) -> None:
        class _FakeBind:
            dialect = type("_Dialect", (), {"name": "mysql"})

        class _FakeSession:
            def get_bind(self) -> _FakeBind:
                return _FakeBind()

        session = cast("AsyncSession", _FakeSession())
        with pytest.raises(ValueError, match="Azure cost persistence does not support"):
            _dialect_insert(session, AzureCostPredictionModel)


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class TestImmutableAzureCostIdentityError:
    def test_is_runtime_error(self) -> None:
        err = ImmutableAzureCostIdentityError("msg")
        assert isinstance(err, RuntimeError)

    def test_message_preserved(self) -> None:
        err = ImmutableAzureCostIdentityError("identity mismatch")
        assert str(err) == "identity mismatch"


class TestStaleAzureCostLeaseError:
    def test_is_runtime_error(self) -> None:
        err = StaleAzureCostLeaseError("stale")
        assert isinstance(err, RuntimeError)

    def test_message_preserved(self) -> None:
        err = StaleAzureCostLeaseError("lease expired")
        assert str(err) == "lease expired"


class TestNonMonotonicAzureCostStateError:
    def test_is_runtime_error(self) -> None:
        err = NonMonotonicAzureCostStateError("wrong")
        assert isinstance(err, RuntimeError)

    def test_message_preserved(self) -> None:
        err = NonMonotonicAzureCostStateError("monotonic violation")
        assert str(err) == "monotonic violation"


# ---------------------------------------------------------------------------
# AzureCostLeaseClaim
# ---------------------------------------------------------------------------


class TestAzureCostLeaseClaim:
    def test_frozen_dataclass(self) -> None:
        claim = AzureCostLeaseClaim(
            prediction_id="p-1",
            prediction_version=1,
            owner="worker-a",
            fencing_token=5,
            expires_at=_now() + timedelta(minutes=5),
        )
        assert claim.prediction_id == "p-1"
        assert claim.fencing_token == 5

    def test_frozen_prevents_mutation(self) -> None:
        claim = AzureCostLeaseClaim(
            prediction_id="p-1",
            prediction_version=1,
            owner="w",
            fencing_token=1,
            expires_at=_now(),
        )
        with pytest.raises(AttributeError) as exc_info:
            claim.fencing_token = 999  # type: ignore[misc]
        assert "fencing_token" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Repository tests (sqlite+aiosqlite)
# ---------------------------------------------------------------------------

# Shared engine/session fixtures


@pytest_asyncio.fixture
async def _engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session(_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(_engine, expire_on_commit=False) as s:
        yield s


@pytest_asyncio.fixture
async def repo(session: AsyncSession) -> AzureCostReconciliationRepository:
    return AzureCostReconciliationRepository(session)


class TestPersistPrediction:
    async def test_inserts_new_prediction(self, repo: AzureCostReconciliationRepository) -> None:
        pred = _prediction()
        now = _now()
        not_before = pred.usage_ended_at + timedelta(days=30)
        row = await repo.persist_prediction(pred, not_before=not_before, now=now)
        assert row.prediction_id == "pred-001"
        assert row.prediction_version == 1
        assert row.state == "PREDICTED"
        assert row.lease_owner is None
        assert row.fencing_token == 0

    async def test_persist_prediction_creates_outbox_event(
        self, repo: AzureCostReconciliationRepository, session: AsyncSession
    ) -> None:
        pred = _prediction()
        now = _now()
        not_before = pred.usage_ended_at + timedelta(days=30)
        await repo.persist_prediction(pred, not_before=not_before, now=now)
        await session.commit()
        stmt = text("SELECT COUNT(*) FROM azure_cost_outbox_events")
        result = await session.execute(stmt)
        count: int = result.scalar_one()
        assert count == 1

    async def test_idempotent_re_insert_returns_identical(self, repo: AzureCostReconciliationRepository) -> None:
        pred = _prediction()
        now = _now()
        not_before = pred.usage_ended_at + timedelta(days=30)
        row1 = await repo.persist_prediction(pred, not_before=not_before, now=now)
        row2 = await repo.persist_prediction(pred, not_before=not_before, now=now)
        assert row1.identity_fingerprint == row2.identity_fingerprint

    async def test_identity_mismatch_raises(
        self, repo: AzureCostReconciliationRepository, session: AsyncSession
    ) -> None:
        """Insert a row, then set wrong fingerprint; re-insert with same PK should raise."""
        pred = _prediction()
        now = _now()
        not_before = pred.usage_ended_at + timedelta(days=30)
        row = await repo.persist_prediction(pred, not_before=not_before, now=now)
        await session.commit()
        row.identity_fingerprint = "00" * 32
        await session.commit()
        with pytest.raises(ImmutableAzureCostIdentityError):
            await repo.persist_prediction(pred, not_before=not_before, now=now)

    async def test_not_before_must_not_precede_usage_ended(self, repo: AzureCostReconciliationRepository) -> None:
        pred = _prediction()
        now = _now()
        with pytest.raises(ValueError, match="not_before"):
            await repo.persist_prediction(pred, not_before=pred.usage_ended_at - timedelta(days=1), now=now)

    async def test_requires_aware_datetimes(self, repo: AzureCostReconciliationRepository) -> None:
        pred = _prediction()
        with pytest.raises(ValueError, match="timezone-aware"):
            await repo.persist_prediction(pred, not_before=datetime(2026, 1, 1), now=_now())


class TestClaimDue:
    async def _seed_prediction(
        self,
        repo: AzureCostReconciliationRepository,
        *,
        prediction_id: str = "pred-001",
    ) -> None:
        pred = _prediction(prediction_id=prediction_id)
        not_before = pred.usage_ended_at + timedelta(days=30)
        await repo.persist_prediction(pred, not_before=not_before, now=_now())

    async def test_claims_due_rows(self, repo: AzureCostReconciliationRepository) -> None:
        await self._seed_prediction(repo)
        claims = await repo.claim_due(
            owner="w1", now=_now() + timedelta(days=60), lease_duration=timedelta(minutes=5), limit=100
        )
        assert len(claims) == 1
        claim = claims[0]
        assert claim.prediction_id == "pred-001"
        assert claim.owner == "w1"
        assert claim.fencing_token == 1

    async def test_claim_increments_fencing_token(self, repo: AzureCostReconciliationRepository) -> None:
        await self._seed_prediction(repo)
        now60 = _now() + timedelta(days=60)
        c1 = await repo.claim_due(owner="w1", now=now60, lease_duration=timedelta(minutes=5), limit=100)
        c2 = await repo.claim_due(
            owner="w1", now=now60 + timedelta(hours=1), lease_duration=timedelta(minutes=5), limit=100
        )
        assert c1[0].fencing_token == 1
        if c2:
            assert c2[0].fencing_token == 2

    async def test_not_before_past_blocks_claim(self, repo: AzureCostReconciliationRepository) -> None:
        """prediction persisted with not_before 30 days past usage_ended;
        claiming at now=usage_ended+1 day should return 0."""
        pred = _prediction()
        not_before = pred.usage_ended_at + timedelta(days=30)
        await repo.persist_prediction(pred, not_before=not_before, now=_now())
        claims = await repo.claim_due(
            owner="w1", now=pred.usage_ended_at + timedelta(days=1), lease_duration=timedelta(minutes=5), limit=100
        )
        assert claims == []

    async def test_active_lease_blocks_re_claim(self, repo: AzureCostReconciliationRepository) -> None:
        await self._seed_prediction(repo)
        now60 = _now() + timedelta(days=60)
        c1 = await repo.claim_due(owner="w1", now=now60, lease_duration=timedelta(minutes=15), limit=100)
        assert len(c1) == 1
        c2 = await repo.claim_due(
            owner="w2", now=now60 + timedelta(minutes=5), lease_duration=timedelta(minutes=5), limit=100
        )
        assert c2 == []

    async def test_expired_lease_allows_re_claim(self, repo: AzureCostReconciliationRepository) -> None:
        await self._seed_prediction(repo)
        now60 = _now() + timedelta(days=60)
        await repo.claim_due(owner="w1", now=now60, lease_duration=timedelta(minutes=2), limit=100)
        after_expiry = now60 + timedelta(minutes=3)
        claims = await repo.claim_due(owner="w2", now=after_expiry, lease_duration=timedelta(minutes=5), limit=100)
        assert len(claims) == 1
        assert claims[0].owner == "w2"

    async def test_filter_by_prediction_id(self, repo: AzureCostReconciliationRepository) -> None:
        await self._seed_prediction(repo, prediction_id="p-a")
        await self._seed_prediction(repo, prediction_id="p-b")
        now60 = _now() + timedelta(days=60)
        claims = await repo.claim_due(
            owner="w1", now=now60, lease_duration=timedelta(minutes=5), limit=100, prediction_id="p-a"
        )
        assert len(claims) == 1
        assert claims[0].prediction_id == "p-a"

    async def test_empty_owner_raises(self, repo: AzureCostReconciliationRepository) -> None:
        with pytest.raises(ValueError, match="owner"):
            await repo.claim_due(owner="   ", now=_now(), lease_duration=timedelta(minutes=5), limit=10)

    async def test_zero_lease_duration_raises(self, repo: AzureCostReconciliationRepository) -> None:
        with pytest.raises(ValueError, match="lease_duration"):
            await repo.claim_due(owner="w1", now=_now(), lease_duration=timedelta(0), limit=10)

    async def test_negative_limit_raises(self, repo: AzureCostReconciliationRepository) -> None:
        with pytest.raises(ValueError, match="limit"):
            await repo.claim_due(owner="w1", now=_now(), lease_duration=timedelta(minutes=5), limit=0)

    async def test_limit_exceeds_max_raises(self, repo: AzureCostReconciliationRepository) -> None:
        with pytest.raises(ValueError, match="limit"):
            await repo.claim_due(owner="w1", now=_now(), lease_duration=timedelta(minutes=5), limit=2000)


class TestUpsertActualCost:
    async def _seed_and_claim(self, repo: AzureCostReconciliationRepository) -> AzureCostLeaseClaim:
        pred = _prediction()
        not_before = pred.usage_ended_at + timedelta(days=30)
        await repo.persist_prediction(pred, not_before=not_before, now=_now())
        claims = await repo.claim_due(
            owner="w1", now=_now() + timedelta(days=60), lease_duration=timedelta(minutes=15), limit=1
        )
        return claims[0]

    async def test_inserts_observation(self, repo: AzureCostReconciliationRepository) -> None:
        claim = await self._seed_and_claim(repo)
        obs = _observation()
        row = await repo.upsert_actual_cost(claim, obs, now=_now() + timedelta(days=60))
        assert row.prediction_id == "pred-001"
        assert row.source == "cost-management"
        assert row.cost_usd == Decimal("10.0")

    async def test_stale_lease_raises(self, repo: AzureCostReconciliationRepository) -> None:
        claim = await self._seed_and_claim(repo)
        far_future = _now() + timedelta(days=1000)
        obs = _observation()
        with pytest.raises(StaleAzureCostLeaseError):
            await repo.upsert_actual_cost(claim, obs, now=far_future)

    async def test_idempotent_observation_insert(self, repo: AzureCostReconciliationRepository) -> None:
        claim = await self._seed_and_claim(repo)
        obs = _observation()
        now60 = _now() + timedelta(days=60)
        r1 = await repo.upsert_actual_cost(claim, obs, now=now60)
        claim2 = AzureCostLeaseClaim(
            prediction_id=claim.prediction_id,
            prediction_version=claim.prediction_version,
            owner=claim.owner,
            fencing_token=claim.fencing_token,
            expires_at=claim.expires_at,
        )
        r2 = await repo.upsert_actual_cost(claim2, obs, now=now60 + timedelta(seconds=1))
        assert r1.payload_fingerprint == r2.payload_fingerprint


class TestAdvanceState:
    async def _seed_and_claim(self, repo: AzureCostReconciliationRepository) -> AzureCostLeaseClaim:
        pred = _prediction()
        not_before = pred.usage_ended_at + timedelta(days=30)
        await repo.persist_prediction(pred, not_before=not_before, now=_now())
        claims = await repo.claim_due(
            owner="w1", now=_now() + timedelta(days=60), lease_duration=timedelta(minutes=15), limit=1
        )
        return claims[0]

    async def test_advance_to_usage_pending(self, repo: AzureCostReconciliationRepository) -> None:
        claim = await self._seed_and_claim(repo)
        now60 = _now() + timedelta(days=60)
        row = await repo.advance_state(claim, AzureCostLedgerState.USAGE_PENDING, now=now60)
        assert row.state == "USAGE_PENDING"

    async def test_advance_to_stable_via_intermediates(
        self,
        repo: AzureCostReconciliationRepository,
    ) -> None:
        claim = await self._seed_and_claim(repo)
        claimed_at = _now() + timedelta(days=60)
        states = ("USAGE_PENDING", "QUERY_DUE", "PARTIAL", "PROVISIONAL", "STABLE")

        for offset, state_name in enumerate(states):
            state = AzureCostLedgerState(state_name)
            row = await repo.advance_state(
                claim,
                state,
                now=claimed_at + timedelta(minutes=offset),
            )
            assert row.state == state_name

    async def test_forged_later_expiry_is_rejected_while_database_lease_is_active(
        self,
        repo: AzureCostReconciliationRepository,
    ) -> None:
        claim = await self._seed_and_claim(repo)
        forged = AzureCostLeaseClaim(
            prediction_id=claim.prediction_id,
            prediction_version=claim.prediction_version,
            owner=claim.owner,
            fencing_token=claim.fencing_token,
            expires_at=claim.expires_at + timedelta(minutes=1),
        )

        with pytest.raises(StaleAzureCostLeaseError, match="stale"):
            await repo.advance_state(
                forged,
                AzureCostLedgerState.USAGE_PENDING,
                now=_now() + timedelta(days=60),
            )

    async def test_forged_earlier_expiry_is_rejected_before_database_expiry(
        self,
        repo: AzureCostReconciliationRepository,
    ) -> None:
        claim = await self._seed_and_claim(repo)
        forged = AzureCostLeaseClaim(
            prediction_id=claim.prediction_id,
            prediction_version=claim.prediction_version,
            owner=claim.owner,
            fencing_token=claim.fencing_token,
            expires_at=claim.expires_at - timedelta(minutes=10),
        )

        with pytest.raises(StaleAzureCostLeaseError, match="stale"):
            await repo.advance_state(
                forged,
                AzureCostLedgerState.USAGE_PENDING,
                now=claim.expires_at - timedelta(minutes=5),
            )

    async def test_naive_claim_expiry_is_rejected(
        self,
        repo: AzureCostReconciliationRepository,
    ) -> None:
        claim = await self._seed_and_claim(repo)
        forged = AzureCostLeaseClaim(
            prediction_id=claim.prediction_id,
            prediction_version=claim.prediction_version,
            owner=claim.owner,
            fencing_token=claim.fencing_token,
            expires_at=claim.expires_at.replace(tzinfo=None),
        )

        with pytest.raises(ValueError, match=r"claim\.expires_at must be timezone-aware"):
            await repo.advance_state(
                forged,
                AzureCostLedgerState.USAGE_PENDING,
                now=_now() + timedelta(days=60),
            )

    async def test_exact_lease_expiry_boundary_is_rejected(
        self,
        repo: AzureCostReconciliationRepository,
    ) -> None:
        claim = await self._seed_and_claim(repo)

        with pytest.raises(StaleAzureCostLeaseError, match="stale"):
            await repo.advance_state(
                claim,
                AzureCostLedgerState.USAGE_PENDING,
                now=claim.expires_at,
            )

    async def test_wrong_owner_and_fencing_token_are_rejected(
        self,
        repo: AzureCostReconciliationRepository,
    ) -> None:
        claim = await self._seed_and_claim(repo)
        claimed_at = _now() + timedelta(days=60)
        forged_claims = (
            AzureCostLeaseClaim(
                prediction_id=claim.prediction_id,
                prediction_version=claim.prediction_version,
                owner="different-worker",
                fencing_token=claim.fencing_token,
                expires_at=claim.expires_at,
            ),
            AzureCostLeaseClaim(
                prediction_id=claim.prediction_id,
                prediction_version=claim.prediction_version,
                owner=claim.owner,
                fencing_token=claim.fencing_token + 1,
                expires_at=claim.expires_at,
            ),
        )

        for forged in forged_claims:
            with pytest.raises(StaleAzureCostLeaseError, match="stale"):
                await repo.advance_state(
                    forged,
                    AzureCostLedgerState.USAGE_PENDING,
                    now=claimed_at,
                )

    async def test_expired_owner_is_fenced_after_takeover(
        self,
        repo: AzureCostReconciliationRepository,
    ) -> None:
        first = await self._seed_and_claim(repo)
        takeover_at = first.expires_at
        successors = await repo.claim_due(
            owner="replacement-worker",
            now=takeover_at,
            lease_duration=timedelta(minutes=15),
            limit=1,
        )
        assert len(successors) == 1
        successor = successors[0]
        assert successor.fencing_token == first.fencing_token + 1

        with pytest.raises(StaleAzureCostLeaseError, match="stale"):
            await repo.advance_state(
                first,
                AzureCostLedgerState.USAGE_PENDING,
                now=takeover_at,
            )

        row = await repo.advance_state(
            successor,
            AzureCostLedgerState.USAGE_PENDING,
            now=takeover_at,
        )
        assert row.state == AzureCostLedgerState.USAGE_PENDING.value

    async def test_non_monotonic_raises(self, repo: AzureCostReconciliationRepository) -> None:
        claim = await self._seed_and_claim(repo)
        now60 = _now() + timedelta(days=60)
        await repo.advance_state(claim, AzureCostLedgerState.USAGE_PENDING, now=now60)
        with pytest.raises(NonMonotonicAzureCostStateError):
            await repo.advance_state(claim, AzureCostLedgerState.PREDICTED, now=now60)

    async def test_final_requires_stable(self, repo: AzureCostReconciliationRepository) -> None:
        claim = await self._seed_and_claim(repo)
        now60 = _now() + timedelta(days=60)
        with pytest.raises(NonMonotonicAzureCostStateError, match="STABLE"):
            await repo.advance_state(claim, AzureCostLedgerState.FINAL, now=now60)

    async def test_adjusted_requires_final(self, repo: AzureCostReconciliationRepository) -> None:
        claim = await self._seed_and_claim(repo)
        now60 = _now() + timedelta(days=60)
        await repo.advance_state(claim, AzureCostLedgerState.USAGE_PENDING, now=now60)
        with pytest.raises(NonMonotonicAzureCostStateError, match="FINAL"):
            await repo.advance_state(claim, AzureCostLedgerState.ADJUSTED, now=now60)

    async def test_idempotent_advance(self, repo: AzureCostReconciliationRepository) -> None:
        claim = await self._seed_and_claim(repo)
        now60 = _now() + timedelta(days=60)
        await repo.advance_state(claim, AzureCostLedgerState.USAGE_PENDING, now=now60)
        row = await repo.advance_state(
            claim,
            AzureCostLedgerState.USAGE_PENDING,
            now=now60,
        )
        assert row.state == "USAGE_PENDING"

    async def test_final_advance_from_stable(self, repo: AzureCostReconciliationRepository) -> None:
        claim = await self._seed_and_claim(repo)
        now60 = _now() + timedelta(days=60)
        for state_name in ("USAGE_PENDING", "QUERY_DUE", "PARTIAL", "PROVISIONAL", "STABLE"):
            state = AzureCostLedgerState(state_name)
            await repo.advance_state(claim, state, now=now60)
        final = await repo.advance_state(claim, AzureCostLedgerState.FINAL, now=now60)
        assert final.state == "FINAL"
        assert final.finalized_at is not None

    async def test_advance_outbox_event_created(
        self, repo: AzureCostReconciliationRepository, session: AsyncSession
    ) -> None:
        claim = await self._seed_and_claim(repo)
        now60 = _now() + timedelta(days=60)
        await repo.advance_state(claim, AzureCostLedgerState.USAGE_PENDING, now=now60)
        await session.commit()
        stmt = text(
            "SELECT COUNT(*) FROM azure_cost_outbox_events WHERE event_type = 'COST_RECONCILIATION_USAGE_PENDING'"
        )
        result = await session.execute(stmt)
        count: int = result.scalar_one()
        assert count >= 1


class TestPredictionModelIdProperty:
    async def test_id_property_returns_composite(self, repo: AzureCostReconciliationRepository) -> None:
        pred = _prediction()
        not_before = pred.usage_ended_at + timedelta(days=30)
        row = await repo.persist_prediction(pred, not_before=not_before, now=_now())
        assert row.id == ("pred-001", 1)
