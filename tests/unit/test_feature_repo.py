"""Unit tests for FeatureModel + FeatureRepository — in-memory aiosqlite.

Mirrors tests/unit/test_agent_message_repo.py. Uses an in-memory aiosqlite
engine so no PostgreSQL is needed. Engine is disposed in finally-equivalent
asynccontextmanager fixture.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base, FeatureModel, FeatureStatus
from general_ludd.db.repository import FeatureRepository


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
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield engine
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def async_session(async_engine) -> AsyncSession:
    session_factory = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


class TestFeatureModel:
    @pytest.mark.asyncio
    async def test_create_feature_defaults(self, async_session: AsyncSession):
        feat = FeatureModel(
            name="gludd_facts",
            description="Facts API via MQ",
            category="api",
            status=FeatureStatus.REQUESTED,
        )
        async_session.add(feat)
        await async_session.flush()
        assert feat.id.startswith("FEAT-")
        assert feat.status == FeatureStatus.REQUESTED
        assert feat.acceptance_criteria == "[]"
        assert feat.evidence == "[]"
        assert isinstance(feat.requested_at, datetime)

    @pytest.mark.asyncio
    async def test_feature_status_values(self, async_session: AsyncSession):
        """All FeatureStatus enum values are valid column values."""
        for status in FeatureStatus:
            feat = FeatureModel(
                name=f"feat_{status.value}",
                description="test",
                category="test",
                status=status,
            )
            async_session.add(feat)
        await async_session.flush()


class TestFeatureRepository:
    @pytest.mark.asyncio
    async def test_upsert_creates_new(self, async_session: AsyncSession):
        repo = FeatureRepository(async_session)
        feat = await repo.upsert({
            "name": "message_queue",
            "description": "MQ feature",
            "category": "core",
            "status": FeatureStatus.IMPLEMENTED,
            "evidence": ["module:gludd_message"],
            "acceptance_criteria": ["MQ module exists"],
        })
        assert feat.id.startswith("FEAT-")
        assert feat.name == "message_queue"
        # evidence is serialized to JSON string
        assert "gludd_message" in feat.evidence

    @pytest.mark.asyncio
    async def test_upsert_updates_existing(self, async_session: AsyncSession):
        repo = FeatureRepository(async_session)
        # First upsert creates the feature
        feat1 = await repo.upsert({
            "name": "ci_green",
            "description": "original",
            "category": "ci",
            "status": FeatureStatus.REQUESTED,
            "evidence": [],
            "acceptance_criteria": [],
        })
        feat1_id = feat1.id
        # Second upsert with same name updates fields
        feat2 = await repo.upsert({
            "name": "ci_green",
            "description": "updated description",
            "category": "ci",
            "status": FeatureStatus.IMPLEMENTED,
            "evidence": ["test:tests/integration/test_ci.py"],
            "acceptance_criteria": ["CI gate green"],
        })
        assert feat2.id == feat1_id  # same row
        assert feat2.description == "updated description"
        assert feat2.status == FeatureStatus.IMPLEMENTED

    @pytest.mark.asyncio
    async def test_get_by_name(self, async_session: AsyncSession):
        repo = FeatureRepository(async_session)
        await repo.upsert({
            "name": "facts_api_mq",
            "description": "Facts via MQ",
            "category": "api",
            "status": FeatureStatus.IMPLEMENTED,
            "evidence": ["module:gludd_facts"],
            "acceptance_criteria": [],
        })
        fetched = await repo.get_by_name("facts_api_mq")
        assert fetched is not None
        assert fetched.name == "facts_api_mq"

    @pytest.mark.asyncio
    async def test_get_by_name_missing(self, async_session: AsyncSession):
        repo = FeatureRepository(async_session)
        result = await repo.get_by_name("nonexistent_feature")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_by_id(self, async_session: AsyncSession):
        repo = FeatureRepository(async_session)
        feat = await repo.upsert({
            "name": "profile_docs",
            "description": "Profile docs",
            "category": "docs",
            "status": FeatureStatus.IMPLEMENTED,
            "evidence": [],
            "acceptance_criteria": [],
        })
        fetched = await repo.get_by_id(feat.id)
        assert fetched is not None
        assert fetched.id == feat.id

    @pytest.mark.asyncio
    async def test_list_all(self, async_session: AsyncSession):
        repo = FeatureRepository(async_session)
        for i in range(3):
            await repo.upsert({
                "name": f"feature_{i}",
                "description": f"Feature {i}",
                "category": "test",
                "status": FeatureStatus.REQUESTED,
                "evidence": [],
                "acceptance_criteria": [],
            })
        all_feats = await repo.list_all()
        assert len(all_feats) == 3

    @pytest.mark.asyncio
    async def test_list_by_status(self, async_session: AsyncSession):
        repo = FeatureRepository(async_session)
        await repo.upsert({
            "name": "verified_feat",
            "description": "x",
            "category": "c",
            "status": FeatureStatus.VERIFIED,
            "evidence": ["role:security_gate"],
            "acceptance_criteria": [],
        })
        await repo.upsert({
            "name": "requested_feat",
            "description": "y",
            "category": "c",
            "status": FeatureStatus.REQUESTED,
            "evidence": [],
            "acceptance_criteria": [],
        })
        verified = await repo.list_by_status(FeatureStatus.VERIFIED)
        assert len(verified) == 1
        assert verified[0].name == "verified_feat"

        requested = await repo.list_by_status(FeatureStatus.REQUESTED)
        assert len(requested) == 1
        assert requested[0].name == "requested_feat"

    @pytest.mark.asyncio
    async def test_list_by_category(self, async_session: AsyncSession):
        repo = FeatureRepository(async_session)
        await repo.upsert({
            "name": "api_feat",
            "description": "x",
            "category": "api",
            "status": FeatureStatus.REQUESTED,
            "evidence": [],
            "acceptance_criteria": [],
        })
        await repo.upsert({
            "name": "core_feat",
            "description": "y",
            "category": "core",
            "status": FeatureStatus.REQUESTED,
            "evidence": [],
            "acceptance_criteria": [],
        })
        api_feats = await repo.list_by_category("api")
        assert len(api_feats) == 1
        assert api_feats[0].name == "api_feat"

    @pytest.mark.asyncio
    async def test_set_status_roundtrip(self, async_session: AsyncSession):
        repo = FeatureRepository(async_session)
        feat = await repo.upsert({
            "name": "status_test_feat",
            "description": "status test",
            "category": "test",
            "status": FeatureStatus.REQUESTED,
            "evidence": [],
            "acceptance_criteria": [],
        })
        now = datetime.now(UTC)
        updated = await repo.set_status(
            feat.id,
            FeatureStatus.VERIFIED,
            detail={"evidence_results": {"all_met": True}},
            verified_at=now,
        )
        assert updated.status == FeatureStatus.VERIFIED
        assert updated.verified_at is not None
        # last_verify_detail should be JSON-serialized detail
        assert "all_met" in updated.last_verify_detail

    @pytest.mark.asyncio
    async def test_json_deserialization_at_repo_boundary(self, async_session: AsyncSession):
        """upsert accepts Python lists; repo serializes to JSON in DB."""
        repo = FeatureRepository(async_session)
        feat = await repo.upsert({
            "name": "json_feat",
            "description": "tests JSON round-trip",
            "category": "test",
            "status": FeatureStatus.REQUESTED,
            "evidence": ["test:tests/unit/test_foo.py", "role:security_gate"],
            "acceptance_criteria": ["gate green", "role exists"],
        })
        # In DB: stored as JSON string
        assert isinstance(feat.evidence, str)
        assert "test:tests/unit/test_foo.py" in feat.evidence
        # Fetch back and verify it's a JSON string with both values
        fetched = await repo.get_by_name("json_feat")
        assert fetched is not None
        import json
        evidence_list = json.loads(fetched.evidence)
        assert len(evidence_list) == 2
        assert "role:security_gate" in evidence_list
