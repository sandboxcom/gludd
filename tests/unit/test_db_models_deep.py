"""Deep edge-case tests for untested model methods, validators, and relationships.

Covers: UTCDateTime, CheckConstraints, UniqueConstraints, composite PKs,
FK cascading behavior, and properties/methods on models not exercised by
the existing test_db_models.py / test_db_models_blob_length.py suites.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import (
    AgentTokenModel,
    AuditEventModel,
    AzureCostObservationModel,
    AzureCostOutboxEventModel,
    AzureCostPredictionModel,
    Base,
    BucketLeaseModel,
    DeploymentRecordModel,
    EventWorkTransportModel,
    FeatureModel,
    FeatureStatus,
    HumanTodoModel,
    LocationKind,
    MemoryRecordModel,
    ModelCallLogModel,
    ModelPerformanceModel,
    OrnithTrainingPairModel,
    PermissionEscalationRequestModel,
    ProjectModel,
    ProjectRelationshipModel,
    RelationType,
    RemediationActionModel,
    SlurmJobModel,
    SpendRecordModel,
    StsAuditModel,
    TaskReturnModel,
    TodoModel,
    UTCDateTime,
    _len_check,
)

# ── engine / session fixtures ────────────────────────────────────────────


def _make_async_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


@pytest_asyncio.fixture
async def async_engine():
    engine = _make_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def async_session(async_engine) -> AsyncSession:
    factory = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


# ── UTCDateTime edge cases ───────────────────────────────────────────────


class TestUTCDateTimeValidator:
    """UTCDateTime type decorator: naive→UTC, aware→UTC, None passthru."""

    def test_process_bind_param_none_returns_none(self):
        dec = UTCDateTime()
        assert dec.process_bind_param(None, None) is None

    def test_process_bind_param_naive_becomes_utc(self):
        dec = UTCDateTime()
        naive = datetime(2026, 1, 1, 12, 0, 0)
        result = dec.process_bind_param(naive, None)
        assert result.tzinfo == UTC
        assert result.hour == 12
        assert result.replace(tzinfo=None) == naive

    def test_process_bind_param_aware_converts_to_utc(self):
        dec = UTCDateTime()
        from zoneinfo import ZoneInfo

        eastern = ZoneInfo("America/New_York")
        ny_noon = datetime(2026, 1, 1, 12, 0, 0, tzinfo=eastern)
        result = dec.process_bind_param(ny_noon, None)
        assert result.tzinfo == UTC
        assert result.hour != 12

    def test_process_result_value_none_returns_none(self):
        dec = UTCDateTime()
        assert dec.process_result_value(None, None) is None

    def test_process_result_value_naive_becomes_utc(self):
        dec = UTCDateTime()
        naive = datetime(2026, 6, 15, 8, 30, 0)
        result = dec.process_result_value(naive, None)
        assert result.tzinfo == UTC
        assert result.replace(tzinfo=None) == naive

    def test_process_result_value_already_utc_stays_utc(self):
        dec = UTCDateTime()
        utc_dt = datetime(2026, 6, 15, 8, 30, 0, tzinfo=UTC)
        result = dec.process_result_value(utc_dt, None)
        assert result.tzinfo == UTC
        assert result == utc_dt

    def test_process_result_value_aware_converts_to_utc(self):
        dec = UTCDateTime()
        datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC).replace(tzinfo=datetime(2026, 1, 1, tzinfo=UTC).tzinfo)
        from zoneinfo import ZoneInfo

        eastern = ZoneInfo("America/New_York")
        # 8 AM NY summer = 12:00 UTC
        ny = datetime(2026, 6, 15, 8, 0, 0, tzinfo=eastern)
        result = dec.process_result_value(ny, None)
        assert result.tzinfo == UTC
        assert result.hour == 12


# ── _len_check helper ────────────────────────────────────────────────────


class TestLenCheckHelper:
    def test_has_name_attribute(self):
        ck = _len_check("payload", "my_table", max_len=100)
        assert ck.name == "ck_my_table_payload_len"

    def test_uses_default_max_len(self):
        ck = _len_check("col", "tbl")
        assert ck.name == "ck_tbl_col_len"

    def test_check_constraint_is_created(self):
        ck = _len_check("col_x", "tbl_x", max_len=500)
        assert ck is not None
        assert hasattr(ck, "name")
        assert isinstance(ck.name, str)
        assert "tbl_x" in ck.name
        assert "col_x" in ck.name


# ── ProjectRelationshipModel ─────────────────────────────────────────────


class TestProjectRelationshipModel:
    async def test_create_parent_relationship(self, async_session: AsyncSession):
        p1 = ProjectModel(project_id="proj-rel-1", name="R1")
        p2 = ProjectModel(project_id="proj-rel-2", name="R2")
        async_session.add_all([p1, p2])
        await async_session.flush()

        rel = ProjectRelationshipModel(
            project_id="proj-rel-1",
            relation_type=RelationType.PARENT,
            location_kind=LocationKind.GLUDD_PROJECT_NAME,
            location_value="R2",
            related_project_id="proj-rel-2",
            controlled_by_gludd=True,
        )
        async_session.add(rel)
        await async_session.flush()
        assert rel.id.startswith("rel-")
        assert rel.relation_type == RelationType.PARENT.value

    async def test_duplicate_edge_rejected(self, async_session: AsyncSession):
        p1 = ProjectModel(project_id="proj-dup-1", name="D1")
        p2 = ProjectModel(project_id="proj-dup-2", name="D2")
        async_session.add_all([p1, p2])
        await async_session.flush()

        for _ in range(2):
            async_session.add(
                ProjectRelationshipModel(
                    project_id="proj-dup-1",
                    relation_type=RelationType.CHILD,
                    location_kind=LocationKind.GLUDD_PROJECT_NAME,
                    location_value="D2",
                    related_project_id="proj-dup-2",
                )
            )
        with pytest.raises(IntegrityError, match="UNIQUE constraint failed"):
            await async_session.flush()
        await async_session.rollback()

    async def test_external_relation_location_kind_url(self, async_session: AsyncSession):
        p = ProjectModel(project_id="proj-ext-loc", name="ExtLoc")
        async_session.add(p)
        await async_session.flush()

        for location_kind in (
            LocationKind.GLUDD_PROJECT_NAME,
            LocationKind.DIRECTORY,
            LocationKind.URL,
        ):
            rel = ProjectRelationshipModel(
                project_id="proj-ext-loc",
                relation_type=RelationType.CHILD,
                location_kind=location_kind,
                location_value=f"loc-{location_kind}",
            )
            async_session.add(rel)
        await async_session.flush()
        # all three distinct LocationKind values should coexist
        count = (
            (
                await async_session.execute(
                    select(ProjectRelationshipModel).where(ProjectRelationshipModel.project_id == "proj-ext-loc")
                )
            )
            .scalars()
            .all()
        )
        assert len(count) == 3
        await async_session.rollback()

    async def test_duplicate_child_edge_rejected(self, async_session: AsyncSession):
        p1 = ProjectModel(project_id="proj-dup-1", name="D1")
        p2 = ProjectModel(project_id="proj-dup-2", name="D2")
        async_session.add_all([p1, p2])
        await async_session.flush()

        for _ in range(2):
            async_session.add(
                ProjectRelationshipModel(
                    project_id="proj-dup-1",
                    relation_type=RelationType.CHILD,
                    location_kind=LocationKind.GLUDD_PROJECT_NAME,
                    location_value="D2",
                    related_project_id="proj-dup-2",
                )
            )
        with pytest.raises(IntegrityError, match="UNIQUE constraint failed: project_relationships"):
            await async_session.flush()
        assert True  # exception raised as expected
        await async_session.rollback()

    async def test_project_cascade_deletes_relationships(self, async_session: AsyncSession):
        p = ProjectModel(project_id="proj-cas", name="C")
        async_session.add(p)
        await async_session.flush()
        async_session.add(
            ProjectRelationshipModel(
                project_id="proj-cas",
                relation_type=RelationType.CHILD,
                location_kind=LocationKind.DIRECTORY,
                location_value="/tmp/x",
            )
        )
        await async_session.flush()
        await async_session.delete(p)
        await async_session.flush()
        count = (
            (
                await async_session.execute(
                    select(ProjectRelationshipModel).where(ProjectRelationshipModel.project_id == "proj-cas")
                )
            )
            .scalars()
            .all()
        )
        assert len(count) == 0

    async def test_related_project_set_null_on_delete(self, async_session: AsyncSession):
        p1 = ProjectModel(project_id="proj-sn-1", name="SN1")
        p2 = ProjectModel(project_id="proj-sn-2", name="SN2")
        async_session.add_all([p1, p2])
        await async_session.flush()

        rel = ProjectRelationshipModel(
            project_id="proj-sn-1",
            relation_type=RelationType.SIBLING,
            location_kind=LocationKind.GLUDD_PROJECT_NAME,
            location_value="SN2",
            related_project_id="proj-sn-2",
        )
        async_session.add(rel)
        await async_session.flush()

        await async_session.delete(p2)
        await async_session.flush()
        await async_session.refresh(rel)
        assert rel.related_project_id is None

    async def test_external_relation_no_related_project(self, async_session: AsyncSession):
        p = ProjectModel(project_id="proj-ext", name="ExtRel")
        async_session.add(p)
        await async_session.flush()

        rel = ProjectRelationshipModel(
            project_id="proj-ext",
            relation_type=RelationType.EXTERNAL,
            location_kind=LocationKind.URL,
            location_value="https://example.com",
            interface_hint="GET /health",
            interface_contract='{"openapi":"3.0"}',
            controlled_by_gludd=False,
        )
        async_session.add(rel)
        await async_session.flush()
        assert rel.related_project_id is None
        assert rel.interface_hint == "GET /health"
        assert rel.interface_contract == '{"openapi":"3.0"}'
        assert rel.controlled_by_gludd is False


# ── DeploymentRecordModel CheckConstraints ───────────────────────────────


class TestDeploymentRecordModelConstraints:
    async def test_revision_zero_raises(self, async_session: AsyncSession):
        dr = DeploymentRecordModel(
            instance_id="inst-rev0",
            working_dir="/tmp/w",
            revision=0,
        )
        async_session.add(dr)
        with pytest.raises(IntegrityError, match="ck_deployment_records_revision_positive"):
            await async_session.flush()
        await async_session.rollback()

    async def test_revision_positive_succeeds(self, async_session: AsyncSession):
        dr = DeploymentRecordModel(
            instance_id="inst-rev1",
            working_dir="/tmp/w",
            revision=1,
        )
        async_session.add(dr)
        await async_session.flush()
        assert dr.revision == 1

    async def test_destroying_without_owner_raises(self, async_session: AsyncSession):
        dr = DeploymentRecordModel(
            instance_id="inst-destroy-no-owner",
            working_dir="/tmp/w",
            state="destroying",
            destroy_owner=None,
        )
        async_session.add(dr)
        with pytest.raises(IntegrityError, match="ck_deployment_records_destroy_owner_state"):
            await async_session.flush()
        await async_session.rollback()

    async def test_destroy_owner_on_non_destroying_raises(self, async_session: AsyncSession):
        dr = DeploymentRecordModel(
            instance_id="inst-owner-no-destroy",
            working_dir="/tmp/w",
            state="running",
            destroy_owner="worker-1",
        )
        async_session.add(dr)
        with pytest.raises(IntegrityError, match="ck_deployment_records_destroy_owner_state"):
            await async_session.flush()
        await async_session.rollback()

    async def test_destroying_with_owner_succeeds(self, async_session: AsyncSession):
        dr = DeploymentRecordModel(
            instance_id="inst-destroy-ok",
            working_dir="/tmp/w",
            state="destroying",
            destroy_owner="worker-1",
        )
        async_session.add(dr)
        await async_session.flush()
        assert dr.state == "destroying"
        assert dr.destroy_owner == "worker-1"


# ── AzureCostPredictionModel ─────────────────────────────────────────────


class TestAzureCostPredictionModel:
    async def test_composite_id_property(self):
        ap = AzureCostPredictionModel(
            prediction_id="pred-abc",
            prediction_version=3,
            todo_id="TODO-DEADBEEF",
            identity_fingerprint="fp-123",
            identity_payload="{}",
            state="pending",
            not_before=datetime(2026, 1, 1, tzinfo=UTC),
        )
        assert ap.id == ("pred-abc", 3)

    async def test_version_zero_raises(self, async_session: AsyncSession):
        ap = AzureCostPredictionModel(
            prediction_id="pred-v0",
            prediction_version=0,
            todo_id="TODO-DEADBEEF",
            identity_fingerprint="fp-123",
            identity_payload="{}",
            state="pending",
            not_before=datetime(2026, 1, 1, tzinfo=UTC),
        )
        async_session.add(ap)
        with pytest.raises(IntegrityError, match="ck_azure_cost_predictions_version_positive"):
            await async_session.flush()
        await async_session.rollback()

    async def test_fencing_token_negative_raises(self, async_session: AsyncSession):
        ap = AzureCostPredictionModel(
            prediction_id="pred-ft-neg",
            prediction_version=1,
            todo_id="TODO-DEADBEEF",
            identity_fingerprint="fp-123",
            identity_payload="{}",
            state="pending",
            not_before=datetime(2026, 1, 1, tzinfo=UTC),
            fencing_token=-1,
        )
        async_session.add(ap)
        with pytest.raises(IntegrityError, match="ck_azure_cost_predictions_fencing_nonnegative"):
            await async_session.flush()
        await async_session.rollback()

    async def test_state_rank_out_of_range_raises(self, async_session: AsyncSession):
        ap = AzureCostPredictionModel(
            prediction_id="pred-sr8",
            prediction_version=1,
            todo_id="TODO-DEADBEEF",
            identity_fingerprint="fp-123",
            identity_payload="{}",
            state="pending",
            not_before=datetime(2026, 1, 1, tzinfo=UTC),
            state_rank=8,
        )
        async_session.add(ap)
        with pytest.raises(IntegrityError, match="ck_azure_cost_predictions_state_rank"):
            await async_session.flush()
        await async_session.rollback()

    async def test_state_rank_boundary_zero_succeeds(self, async_session: AsyncSession):
        ap = AzureCostPredictionModel(
            prediction_id="pred-sr0",
            prediction_version=1,
            todo_id="TODO-DEADBEEF",
            identity_fingerprint="fp-123",
            identity_payload="{}",
            state="pending",
            not_before=datetime(2026, 1, 1, tzinfo=UTC),
            state_rank=0,
        )
        async_session.add(ap)
        await async_session.flush()
        assert ap.state_rank == 0

    async def test_state_rank_boundary_seven_succeeds(self, async_session: AsyncSession):
        ap = AzureCostPredictionModel(
            prediction_id="pred-sr7",
            prediction_version=1,
            todo_id="TODO-DEADBEEF",
            identity_fingerprint="fp-123",
            identity_payload="{}",
            state="pending",
            not_before=datetime(2026, 1, 1, tzinfo=UTC),
            state_rank=7,
        )
        async_session.add(ap)
        await async_session.flush()
        assert ap.state_rank == 7

    async def test_lease_pair_constraint_null_both_ok(self, async_session: AsyncSession):
        ap = AzureCostPredictionModel(
            prediction_id="pred-lp-null",
            prediction_version=1,
            todo_id="TODO-DEADBEEF",
            identity_fingerprint="fp-123",
            identity_payload="{}",
            state="pending",
            not_before=datetime(2026, 1, 1, tzinfo=UTC),
            lease_owner=None,
            lease_expires_at=None,
        )
        async_session.add(ap)
        await async_session.flush()
        assert ap.lease_owner is None
        assert ap.lease_expires_at is None

    async def test_lease_pair_constraint_both_set_succeeds(self, async_session: AsyncSession):
        expires = datetime(2026, 12, 31, tzinfo=UTC)
        ap = AzureCostPredictionModel(
            prediction_id="pred-lp-both",
            prediction_version=1,
            todo_id="TODO-DEADBEEF",
            identity_fingerprint="fp-123",
            identity_payload="{}",
            state="pending",
            not_before=datetime(2026, 1, 1, tzinfo=UTC),
            lease_owner="worker-1",
            lease_expires_at=expires,
        )
        async_session.add(ap)
        await async_session.flush()
        assert ap.lease_owner == "worker-1"
        assert ap.lease_expires_at == expires

    async def test_lease_pair_constraint_owner_no_expiry_raises(self, async_session: AsyncSession):
        ap = AzureCostPredictionModel(
            prediction_id="pred-lp-own-only",
            prediction_version=1,
            todo_id="TODO-DEADBEEF",
            identity_fingerprint="fp-123",
            identity_payload="{}",
            state="pending",
            not_before=datetime(2026, 1, 1, tzinfo=UTC),
            lease_owner="worker-1",
            lease_expires_at=None,
        )
        async_session.add(ap)
        with pytest.raises(IntegrityError, match="ck_azure_cost_predictions_lease_pair"):
            await async_session.flush()
        await async_session.rollback()

    async def test_lease_pair_constraint_expiry_no_owner_raises(self, async_session: AsyncSession):
        ap = AzureCostPredictionModel(
            prediction_id="pred-lp-exp-only",
            prediction_version=1,
            todo_id="TODO-DEADBEEF",
            identity_fingerprint="fp-123",
            identity_payload="{}",
            state="pending",
            not_before=datetime(2026, 1, 1, tzinfo=UTC),
            lease_owner=None,
            lease_expires_at=datetime(2026, 12, 31, tzinfo=UTC),
        )
        async_session.add(ap)
        with pytest.raises(IntegrityError, match="ck_azure_cost_predictions_lease_pair"):
            await async_session.flush()
        await async_session.rollback()


# ── AzureCostObservationModel ────────────────────────────────────────────


class TestAzureCostObservationModel:
    async def test_fencing_token_zero_raises(self, async_session: AsyncSession):
        pred = AzureCostPredictionModel(
            prediction_id="pred-obs",
            prediction_version=1,
            todo_id="TODO-DEADBEEF",
            identity_fingerprint="fp-123",
            identity_payload="{}",
            state="pending",
            not_before=datetime(2026, 1, 1, tzinfo=UTC),
        )
        async_session.add(pred)
        await async_session.flush()

        obs = AzureCostObservationModel(
            prediction_id="pred-obs",
            prediction_version=1,
            source="azure",
            snapshot_id="snap-1",
            row_identity="row-1",
            cost_usd=Decimal("12.34567890"),
            currency="USD",
            payload_fingerprint="pfp-1",
            payload="{}",
            fencing_token=0,
            observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        async_session.add(obs)
        with pytest.raises(IntegrityError, match="ck_azure_cost_observations_fencing_positive"):
            await async_session.flush()
        await async_session.rollback()

    async def test_fencing_token_positive_succeeds(self, async_session: AsyncSession):
        pred = AzureCostPredictionModel(
            prediction_id="pred-obs2",
            prediction_version=1,
            todo_id="TODO-DEADBEEF",
            identity_fingerprint="fp-456",
            identity_payload="{}",
            state="pending",
            not_before=datetime(2026, 1, 1, tzinfo=UTC),
        )
        async_session.add(pred)
        await async_session.flush()

        obs = AzureCostObservationModel(
            prediction_id="pred-obs2",
            prediction_version=1,
            source="azure",
            snapshot_id="snap-2",
            row_identity="row-2",
            cost_usd=Decimal("99.99999999"),
            currency="USD",
            payload_fingerprint="pfp-2",
            payload="{}",
            fencing_token=42,
            observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        async_session.add(obs)
        await async_session.flush()
        assert obs.cost_usd == Decimal("99.99999999")
        assert obs.currency == "USD"

    async def test_duplicate_observation_identity_raises(self, async_session: AsyncSession):
        pred = AzureCostPredictionModel(
            prediction_id="pred-obs3",
            prediction_version=1,
            todo_id="TODO-DEADBEEF",
            identity_fingerprint="fp-789",
            identity_payload="{}",
            state="pending",
            not_before=datetime(2026, 1, 1, tzinfo=UTC),
        )
        async_session.add(pred)
        await async_session.flush()

        common = dict(
            prediction_id="pred-obs3",
            prediction_version=1,
            source="azure",
            snapshot_id="snap-x",
            row_identity="row-x",
            cost_usd=Decimal("1.00"),
            currency="USD",
            payload_fingerprint="pfp-x",
            payload="{}",
            fencing_token=1,
            observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        async_session.add(AzureCostObservationModel(**common))
        await async_session.flush()
        async_session.add(AzureCostObservationModel(**common))
        with pytest.raises(IntegrityError, match="UNIQUE constraint failed: azure_cost_observations"):
            await async_session.flush()
        await async_session.rollback()


# ── AzureCostOutboxEventModel ────────────────────────────────────────────


class TestAzureCostOutboxEventModel:
    async def test_deduplication_key_unique(self, async_session: AsyncSession):
        pred = AzureCostPredictionModel(
            prediction_id="pred-obx",
            prediction_version=1,
            todo_id="TODO-DEADBEEF",
            identity_fingerprint="fp-obx",
            identity_payload="{}",
            state="pending",
            not_before=datetime(2026, 1, 1, tzinfo=UTC),
        )
        async_session.add(pred)
        await async_session.flush()

        e1 = AzureCostOutboxEventModel(
            event_id="evt-001",
            prediction_id="pred-obx",
            prediction_version=1,
            event_type="prediction_created",
            deduplication_key="dedup-key-1",
            payload="{}",
        )
        async_session.add(e1)
        await async_session.flush()

        e2 = AzureCostOutboxEventModel(
            event_id="evt-002",
            prediction_id="pred-obx",
            prediction_version=1,
            event_type="prediction_updated",
            deduplication_key="dedup-key-1",
            payload="{}",
        )
        async_session.add(e2)
        with pytest.raises(
            IntegrityError, match=r"UNIQUE constraint failed: azure_cost_outbox_events.deduplication_key"
        ):
            await async_session.flush()
        await async_session.rollback()


# ── TodoModel CheckConstraints ───────────────────────────────────────────


class TestTodoModelPriorityConstraints:
    async def test_priority_negative_one_raises(self, async_session: AsyncSession):
        todo = TodoModel(title="Neg priority", priority=-1)
        async_session.add(todo)
        with pytest.raises(IntegrityError, match="ck_todos_priority_range"):
            await async_session.flush()
        await async_session.rollback()

    async def test_priority_1001_raises(self, async_session: AsyncSession):
        todo = TodoModel(title="Over priority", priority=1001)
        async_session.add(todo)
        with pytest.raises(IntegrityError, match="ck_todos_priority_range"):
            await async_session.flush()
        await async_session.rollback()

    async def test_priority_boundary_0_succeeds(self, async_session: AsyncSession):
        todo = TodoModel(title="P0", priority=0)
        async_session.add(todo)
        await async_session.flush()
        assert todo.priority == 0

    async def test_priority_boundary_1000_succeeds(self, async_session: AsyncSession):
        todo = TodoModel(title="P1000", priority=1000)
        async_session.add(todo)
        await async_session.flush()
        assert todo.priority == 1000


# ── MemoryRecordModel ────────────────────────────────────────────────────


class TestMemoryRecordModel:
    async def test_unique_agent_key_ns_project(self, async_session: AsyncSession):
        async_session.add(ProjectModel(project_id="proj-mem", name="Mem"))
        await async_session.flush()

        m1 = MemoryRecordModel(agent_id="agent-a", key="k1", namespace="ns1", project_id="proj-mem", value="v1")
        async_session.add(m1)
        await async_session.flush()

        m2 = MemoryRecordModel(agent_id="agent-a", key="k1", namespace="ns1", project_id="proj-mem", value="v2")
        async_session.add(m2)
        with pytest.raises(IntegrityError, match="UNIQUE constraint failed: memory_records"):
            await async_session.flush()
        await async_session.rollback()

    async def test_different_key_same_rest_succeeds(self, async_session: AsyncSession):
        async_session.add(ProjectModel(project_id="proj-mem2", name="Mem2"))
        await async_session.flush()
        m1 = MemoryRecordModel(agent_id="agent-a", key="k1", namespace="ns1", project_id="proj-mem2", value="v1")
        m2 = MemoryRecordModel(agent_id="agent-a", key="k2", namespace="ns1", project_id="proj-mem2", value="v2")
        async_session.add_all([m1, m2])
        await async_session.flush()
        assert m1.id != m2.id

    async def test_defaults(self, async_session: AsyncSession):
        m = MemoryRecordModel(agent_id="agent-def", key="default-test", value="hello")
        async_session.add(m)
        await async_session.flush()
        assert m.id.startswith("mem-")
        assert m.namespace == "default"
        assert m.ttl_seconds is None

    async def test_null_project_id_allows_same_key(self, async_session: AsyncSession):
        m1 = MemoryRecordModel(agent_id="a", key="global-k", value="v1")
        m2 = MemoryRecordModel(agent_id="a", key="global-k", namespace="other", value="v2")
        async_session.add_all([m1, m2])
        await async_session.flush()
        assert m1.id != m2.id


# ── StsAuditModel ────────────────────────────────────────────────────────


class TestStsAuditModel:
    async def test_token_id_unique(self, async_session: AsyncSession):
        t1 = StsAuditModel(
            token_id="sts-001",
            issuer_agent_id="issuer-1",
            subject_agent_id="subject-1",
            spec_yaml="{}",
            issued_at=1000.0,
            expires_at=2000.0,
        )
        t2 = StsAuditModel(
            token_id="sts-001",
            issuer_agent_id="issuer-2",
            subject_agent_id="subject-2",
            spec_yaml="{}",
            issued_at=1000.0,
            expires_at=2000.0,
        )
        async_session.add_all([t1])
        await async_session.flush()
        async_session.add(t2)
        with pytest.raises(IntegrityError):
            await async_session.flush()
        await async_session.rollback()

    async def test_defaults(self, async_session: AsyncSession):
        t = StsAuditModel(
            token_id="sts-defaults",
            issuer_agent_id="iss-1",
            subject_agent_id="sub-1",
            spec_yaml="{}",
            issued_at=1000.0,
            expires_at=2000.0,
        )
        async_session.add(t)
        await async_session.flush()
        assert t.use_count == 0
        assert t.events == "[]"
        assert t.last_used_at is None


# ── AgentTokenModel ──────────────────────────────────────────────────────


class TestAgentTokenModel:
    async def test_create_and_retrieve(self, async_session: AsyncSession):
        tok = AgentTokenModel(
            token_id="tok-001",
            agent_id="agent-1",
            parent_agent_id="parent-1",
            role_name="developer",
            role_id="role-dev",
            scope_hash="abc123",
            scope_actions='["read","write"]',
        )
        async_session.add(tok)
        await async_session.flush()
        assert tok.hydration_count == 0
        assert tok.revoked_at is None

    async def test_token_id_primary_key_unique(self, async_session: AsyncSession):
        t1 = AgentTokenModel(
            token_id="tok-dup",
            agent_id="a1",
            parent_agent_id="p1",
            role_name="r1",
            role_id="rid1",
        )
        t2 = AgentTokenModel(
            token_id="tok-dup",
            agent_id="a2",
            parent_agent_id="p2",
            role_name="r2",
            role_id="rid2",
        )
        async_session.add(t1)
        await async_session.flush()
        async_session.expunge(t1)
        async_session.add(t2)
        with pytest.raises(IntegrityError):
            await async_session.flush()
        await async_session.rollback()


# ── HumanTodoModel ───────────────────────────────────────────────────────


class TestHumanTodoModel:
    async def test_create_human_todo(self, async_session: AsyncSession):
        ht = HumanTodoModel(
            agent_id="agent-1",
            title="Need API token",
            body="Please provide the API token for the deployment",
            category="human_input",
            priority="high",
        )
        async_session.add(ht)
        await async_session.flush()
        assert ht.id.startswith("HTODO-")
        assert ht.status == "open"
        assert ht.human_resolution is None
        assert ht.human_resolver is None
        assert ht.tags == "[]"

    async def test_parent_agent_todo_fk_set_null(self, async_session: AsyncSession):
        todo = TodoModel(title="Parent todo for human")
        async_session.add(todo)
        await async_session.flush()

        ht = HumanTodoModel(
            parent_agent_todo_id=todo.todo_id,
            agent_id="agent-1",
            title="Need decision",
            category="decision",
            priority="medium",
        )
        async_session.add(ht)
        await async_session.flush()

        await async_session.delete(todo)
        await async_session.flush()
        await async_session.refresh(ht)
        assert ht.parent_agent_todo_id is None

    async def test_defaults(self, async_session: AsyncSession):
        ht = HumanTodoModel(agent_id="a", title="Test defaults", category="human_input")
        async_session.add(ht)
        await async_session.flush()
        assert ht.priority == "medium"
        assert ht.status == "open"
        assert ht.category == "human_input"
        assert ht.body == ""


# ── RemediationActionModel ───────────────────────────────────────────────


class TestRemediationActionModel:
    async def test_create_remediation_action(self, async_session: AsyncSession):
        ra = RemediationActionModel(
            blocked_todo_id="TODO-BLOCKED",
            blocker_kind="human_input",
            action_kind="file_human_todo",
            summary="Filed human todo for escalation",
            detail='{"htodo_id":"HTODO-ABC"}',
            ok=True,
            reason="Escalated after 24h block",
        )
        async_session.add(ra)
        await async_session.flush()
        assert ra.id.startswith("REM-")
        assert ra.ok is True

    async def test_idempotency_key_unique(self, async_session: AsyncSession):
        r1 = RemediationActionModel(
            blocked_todo_id="TODO-IK1",
            blocker_kind="human_input",
            action_kind="schedule_retry",
            idempotency_key="idem-key-001",
        )
        r2 = RemediationActionModel(
            blocked_todo_id="TODO-IK2",
            blocker_kind="permission_escalation",
            action_kind="dispatch_agent",
            idempotency_key="idem-key-001",
        )
        async_session.add(r1)
        await async_session.flush()
        async_session.add(r2)
        with pytest.raises(IntegrityError):
            await async_session.flush()
        await async_session.rollback()


# ── ModelCallLogModel ────────────────────────────────────────────────────


class TestModelCallLogModel:
    async def test_create_model_call_log(self, async_session: AsyncSession):
        log = ModelCallLogModel(
            todo_id="TODO-X",
            job_id="JOB-Y",
            service="openai",
            model_name="gpt-4",
            model_profile_id="profile-default",
            task_type="generation",
            work_type="code",
            success=True,
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.03,
            duration_ms=1200.0,
        )
        async_session.add(log)
        await async_session.flush()
        assert log.id.startswith("MC-")
        assert log.success is True
        assert log.cost_usd == 0.03

    async def test_error_fields_null_on_success(self, async_session: AsyncSession):
        log = ModelCallLogModel(
            service="anthropic",
            model_name="claude-3",
            model_profile_id="profile-claude",
            success=True,
        )
        async_session.add(log)
        await async_session.flush()
        assert log.error_code is None
        assert log.error_message is None

    async def test_error_fields_on_failure(self, async_session: AsyncSession):
        log = ModelCallLogModel(
            service="openai",
            model_name="gpt-4",
            model_profile_id="profile-default",
            success=False,
            error_code="rate_limit",
            error_message="429 Too Many Requests",
        )
        async_session.add(log)
        await async_session.flush()
        assert log.success is False
        assert log.error_code == "rate_limit"
        assert log.error_message == "429 Too Many Requests"

    async def test_defaults(self, async_session: AsyncSession):
        log = ModelCallLogModel(
            service="openai",
            model_name="gpt-4",
            model_profile_id="profile-default",
        )
        async_session.add(log)
        await async_session.flush()
        assert log.task_type == "generation"
        assert log.success is True
        assert log.input_tokens == 0
        assert log.output_tokens == 0
        assert log.cost_usd == 0.0
        assert log.duration_ms == 0.0


# ── ModelPerformanceModel ────────────────────────────────────────────────


class TestModelPerformanceModel:
    async def test_unique_model_profile_id(self, async_session: AsyncSession):
        p1 = ModelPerformanceModel(
            model_profile_id="profile-dup",
            model_name="gpt-3.5",
            service="openai",
        )
        p2 = ModelPerformanceModel(
            model_profile_id="profile-dup",
            model_name="gpt-4",
            service="openai",
        )
        async_session.add(p1)
        await async_session.flush()
        async_session.add(p2)
        with pytest.raises(IntegrityError):
            await async_session.flush()
        await async_session.rollback()

    async def test_defaults(self, async_session: AsyncSession):
        p = ModelPerformanceModel(model_profile_id="profile-def")
        async_session.add(p)
        await async_session.flush()
        assert p.total_calls == 0
        assert p.successful_calls == 0
        assert p.failed_calls == 0
        assert p.total_cost_usd == 0.0
        assert p.avg_duration_ms == 0.0
        assert p.model_name == ""
        assert p.service == ""


# ── OrnithTrainingPairModel ──────────────────────────────────────────────


class TestOrnithTrainingPairModel:
    async def test_create_training_pair(self, async_session: AsyncSession):
        otp = OrnithTrainingPairModel(
            task_description="Fix bug in auth module",
            target_files='["src/auth.py"]',
            scaffold_kind="patch",
            scaffold_content="--- a/src/auth.py\n+++ b/src/auth.py\n...",
            scaffold_hash="abc123def",
            iterations_used=3,
            tokens_consumed=500,
            model_sha="sha256:...",
            agent_id="agent-ornith",
        )
        async_session.add(otp)
        await async_session.flush()
        assert otp.id.startswith("ORN-")
        assert otp.outcome_status == "pending"
        assert otp.outcome_details == "{}"
        assert otp.outcome_set_at is None

    async def test_non_default_project_id(self, async_session: AsyncSession):
        async_session.add(ProjectModel(project_id="proj-orn", name="Orn"))
        await async_session.flush()

        otp = OrnithTrainingPairModel(
            task_description="Test task",
            target_files="[]",
            scaffold_kind="plan",
            scaffold_content="{}",
            scaffold_hash="hash123",
            agent_id="agent-orn",
            project_id="proj-orn",
        )
        async_session.add(otp)
        await async_session.flush()
        assert otp.project_id == "proj-orn"


# ── SlurmJobModel ────────────────────────────────────────────────────────


class TestSlurmJobModel:
    async def test_create_slurm_job(self, async_session: AsyncSession):
        sj = SlurmJobModel(
            job_id="123456",
            deployment_id="dep-1",
            account="acct-1",
            qos="high",
            partition="gpu",
            gpu_count=4,
            gpu_type="a100",
            max_hours=8.0,
            max_cost_usd=50.0,
            hourly_rate_usd=6.25,
            daemon_pid=99999,
        )
        async_session.add(sj)
        await async_session.flush()
        assert sj.status == "submitted"
        assert sj.cost_incurred == 0.0

    async def test_job_id_not_unique_can_be_reused(self, async_session: AsyncSession):
        sj1 = SlurmJobModel(
            job_id="999999",
            deployment_id="dep-a",
            daemon_pid=1,
        )
        sj2 = SlurmJobModel(
            job_id="999999",
            deployment_id="dep-b",
            daemon_pid=1,
        )
        async_session.add_all([sj1, sj2])
        await async_session.flush()
        assert sj1.id != sj2.id
        assert sj1.job_id == sj2.job_id


# ── EventWorkTransportModel ──────────────────────────────────────────────


class TestEventWorkTransportModel:
    async def test_create_event_work_transport(self, async_session: AsyncSession):
        ewt = EventWorkTransportModel(
            event_type="todo.created",
            payload='{"todo_id":"TODO-X"}',
        )
        async_session.add(ewt)
        await async_session.flush()
        assert ewt.status == "pending"
        assert ewt.attempts == 0
        assert ewt.claimed_by is None
        assert ewt.claimed_at is None

    async def test_payload_over_65536_raises(self, async_session: AsyncSession):
        ewt = EventWorkTransportModel(
            event_type="large.payload",
            payload="x" * 65537,
        )
        async_session.add(ewt)
        with pytest.raises(IntegrityError, match="ck_event_work_transport_payload_len"):
            await async_session.flush()
        await async_session.rollback()

    async def test_payload_at_65536_succeeds(self, async_session: AsyncSession):
        ewt = EventWorkTransportModel(
            event_type="max.payload",
            payload="x" * 65536,
        )
        async_session.add(ewt)
        await async_session.flush()
        assert len(ewt.payload) == 65536


# ── PermissionEscalationRequestModel ─────────────────────────────────────


class TestPermissionEscalationRequestModel:
    async def test_create_permission_request(self, async_session: AsyncSession):
        per = PermissionEscalationRequestModel(
            agent_id="agent-1",
            current_spec_yaml="read: [*]",
            requested_capabilities_yaml="write: [src/**]",
            reason="Need write access to src/ for code changes",
            alternatives_tried_json='[{"approach":"read-only workaround","outcome":"insufficient"}]',
        )
        async_session.add(per)
        await async_session.flush()
        assert per.status == "pending"
        assert per.human_reviewer is None
        assert per.decided_at is None

    async def test_alternatives_defaults_to_empty_json(self, async_session: AsyncSession):
        per = PermissionEscalationRequestModel(
            agent_id="agent-2",
            current_spec_yaml="{}",
            requested_capabilities_yaml="{}",
            reason="Test",
        )
        async_session.add(per)
        await async_session.flush()
        assert per.alternatives_tried_json == "[]"


# ── FeatureModel ─────────────────────────────────────────────────────────


class TestFeatureModelEdgeCases:
    async def test_unique_name(self, async_session: AsyncSession):
        f1 = FeatureModel(name="unique-feat-1")
        f2 = FeatureModel(name="unique-feat-1")
        async_session.add(f1)
        await async_session.flush()
        async_session.add(f2)
        with pytest.raises(IntegrityError):
            await async_session.flush()
        await async_session.rollback()

    async def test_defaults(self, async_session: AsyncSession):
        f = FeatureModel(name="feat-defaults")
        async_session.add(f)
        await async_session.flush()
        assert f.id.startswith("FEAT-")
        assert f.category == "general"
        assert f.status == FeatureStatus.REQUESTED.value
        assert f.acceptance_criteria == "[]"
        assert f.evidence == "[]"
        assert f.verifier_kind == "evidence"
        assert f.requested_by == "agent"
        assert f.last_verify_detail == "{}"


# ── AuditEventModel ornith fields ────────────────────────────────────────


class TestAuditEventModelOrnithFields:
    async def test_ornith_audit_fields_default_to_none(self, async_session: AsyncSession):
        ae = AuditEventModel(
            event_type="test_event",
            entity_type="todo",
            entity_id="TODO-ORNITH",
            details="{}",
        )
        async_session.add(ae)
        await async_session.flush()
        assert ae.scaffold_sha256 is None
        assert ae.model is None
        assert ae.tokens_in is None
        assert ae.tokens_out is None

    async def test_ornith_audit_fields_populated(self, async_session: AsyncSession):
        ae = AuditEventModel(
            event_type="ornith_scaffold",
            entity_type="scaffold",
            entity_id="SCAFFOLD-001",
            details="{}",
            scaffold_sha256="abc123",
            model="gpt-4",
            tokens_in=500,
            tokens_out=200,
        )
        async_session.add(ae)
        await async_session.flush()
        assert ae.scaffold_sha256 == "abc123"
        assert ae.model == "gpt-4"
        assert ae.tokens_in == 500
        assert ae.tokens_out == 200


# ── SpendRecordModel ─────────────────────────────────────────────────────


class TestSpendRecordModel:
    async def test_create_spend_record(self, async_session: AsyncSession):
        sr = SpendRecordModel(
            ts=1700000000.123,
            cost_usd=0.05,
            kind="api_call",
            model="gpt-4",
        )
        async_session.add(sr)
        await async_session.flush()
        assert sr.kind == "api_call"
        assert sr.cost_usd == 0.05
        assert sr.ts == 1700000000.123

    async def test_project_fk_set_null_on_delete(self, async_session: AsyncSession):
        proj = ProjectModel(project_id="proj-spend", name="Spend")
        async_session.add(proj)
        await async_session.flush()

        sr = SpendRecordModel(
            project_id="proj-spend",
            ts=1700000000.0,
            cost_usd=0.01,
            kind="api_call",
        )
        async_session.add(sr)
        await async_session.flush()

        await async_session.delete(proj)
        await async_session.flush()
        await async_session.refresh(sr)
        assert sr.project_id is None


# ── BucketLeaseModel uniqueness ──────────────────────────────────────────


class TestBucketLeaseModelUniqueness:
    async def test_unique_bucket_key_holder_id(self, async_session: AsyncSession):
        l1 = BucketLeaseModel(
            bucket_key="todo:core:active",
            holder_id="worker-1",
            expires_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        l2 = BucketLeaseModel(
            bucket_key="todo:core:active",
            holder_id="worker-1",
            expires_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
        async_session.add(l1)
        await async_session.flush()
        async_session.add(l2)
        with pytest.raises(IntegrityError, match="UNIQUE constraint failed: bucket_leases"):
            await async_session.flush()
        await async_session.rollback()

    async def test_different_holders_same_bucket_succeeds(self, async_session: AsyncSession):
        l1 = BucketLeaseModel(
            bucket_key="todo:core:active",
            holder_id="worker-1",
            expires_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        l2 = BucketLeaseModel(
            bucket_key="todo:core:active",
            holder_id="worker-2",
            expires_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
        async_session.add_all([l1, l2])
        await async_session.flush()
        assert l1.id != l2.id


# ── TaskReturnModel schema_version / queue ───────────────────────────────


class TestTaskReturnModelEdgeCases:
    async def test_schema_version_default(self, async_session: AsyncSession):
        tr = TaskReturnModel(
            return_id="R-schema",
            job_id="J-schema",
            playbook="test.yml",
            queue="core",
        )
        async_session.add(tr)
        await async_session.flush()
        assert tr.schema_version == 1

    async def test_missing_todo_id_allowed(self, async_session: AsyncSession):
        tr = TaskReturnModel(
            return_id="R-no-todo",
            job_id="J-no-todo",
            playbook="test.yml",
            queue="core",
            todo_id=None,
        )
        async_session.add(tr)
        await async_session.flush()
        assert tr.todo_id is None
