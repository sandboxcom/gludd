"""Regression tests for XT-2/5/6/7: FeatureRepository must filter by project_id.

Before the fix, FeatureRepository had no project scoping, so the feature-list
endpoints (`GET /api/features`, `GET /api/features/{id}`, `POST /api/features/
verify`) and the facts `_features_facet` returned features across ALL tenants.
The repository now supports a `scoped(session, project_id)` constructor plus a
per-method `project_id` override on get_by_id / list_all / list_by_status /
list_by_category, mirroring TodoRepository.

These tests exercise the repository layer directly with a dedicated in-memory
engine (no FK pragma, so arbitrary project_id strings can be seeded without
ProjectModel rows — the unit under test is the WHERE-clause filter, not the FK).
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base, FeatureModel, FeatureStatus
from general_ludd.db.repository import FeatureRepository


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        # proj-a: 2 features (one verified/security, one requested/general)
        s.add(
            FeatureModel(
                id="FEAT-A1",
                project_id="proj-a",
                name="a-one",
                category="security",
                status=FeatureStatus.VERIFIED.value,
            )
        )
        s.add(
            FeatureModel(
                id="FEAT-A2",
                project_id="proj-a",
                name="a-two",
                category="general",
                status=FeatureStatus.REQUESTED.value,
            )
        )
        # proj-b: 1 feature (verified/security — same status+category as A1)
        s.add(
            FeatureModel(
                id="FEAT-B1",
                project_id="proj-b",
                name="b-one",
                category="security",
                status=FeatureStatus.VERIFIED.value,
            )
        )
        # unscoped (no project): 1 feature
        s.add(
            FeatureModel(
                id="FEAT-N1",
                project_id=None,
                name="n-one",
                category="general",
                status=FeatureStatus.REQUESTED.value,
            )
        )
        await s.commit()
        yield s
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def test_scoped_list_all_returns_only_that_project(session: AsyncSession) -> None:
    repo = FeatureRepository.scoped(session, "proj-a")
    rows = await repo.list_all()
    assert {r.id for r in rows} == {"FEAT-A1", "FEAT-A2"}


async def test_scoped_list_by_status_is_project_filtered(session: AsyncSession) -> None:
    # Both FEAT-A1 (proj-a) and FEAT-B1 (proj-b) are VERIFIED; scoping to proj-a
    # must exclude proj-b's verified feature.
    repo = FeatureRepository.scoped(session, "proj-a")
    rows = await repo.list_by_status(FeatureStatus.VERIFIED)
    assert {r.id for r in rows} == {"FEAT-A1"}


async def test_scoped_list_by_category_is_project_filtered(session: AsyncSession) -> None:
    # Both FEAT-A1 and FEAT-B1 are category "security".
    repo = FeatureRepository.scoped(session, "proj-a")
    rows = await repo.list_by_category("security")
    assert {r.id for r in rows} == {"FEAT-A1"}


async def test_scoped_get_by_id_blocks_cross_tenant(session: AsyncSession) -> None:
    # A repo scoped to proj-a must NOT be able to read proj-b's feature.
    repo = FeatureRepository.scoped(session, "proj-a")
    assert await repo.get_by_id("FEAT-B1") is None
    # ...but it can read its own.
    own = await repo.get_by_id("FEAT-A1")
    assert own is not None and own.id == "FEAT-A1"


async def test_scoped_get_by_name_blocks_cross_tenant(session: AsyncSession) -> None:
    """Name lookups must honor the same tenant scope as ID lookups."""
    repo = FeatureRepository.scoped(session, "proj-a")
    assert await repo.get_by_name("b-one") is None
    own = await repo.get_by_name("a-one")
    assert own is not None and own.id == "FEAT-A1"


async def test_unscoped_list_all_returns_everything(session: AsyncSession) -> None:
    # Back-compat: an unscoped repo (admin path) still sees all tenants' rows.
    repo = FeatureRepository(session)
    rows = await repo.list_all()
    assert {r.id for r in rows} == {"FEAT-A1", "FEAT-A2", "FEAT-B1", "FEAT-N1"}


async def test_per_call_project_id_override(session: AsyncSession) -> None:
    # An unscoped repo can still scope a single call via the project_id param.
    repo = FeatureRepository(session)
    rows = await repo.list_all(project_id="proj-b")
    assert {r.id for r in rows} == {"FEAT-B1"}


async def test_explicit_project_id_overrides_instance_scope(session: AsyncSession) -> None:
    # _resolve_pid prefers the explicit arg over the instance scope.
    repo = FeatureRepository.scoped(session, "proj-a")
    rows = await repo.list_all(project_id="proj-b")
    assert {r.id for r in rows} == {"FEAT-B1"}


@pytest.mark.parametrize("status", [FeatureStatus.VERIFIED, FeatureStatus.REQUESTED])
async def test_scoped_set_status_internal_get_stays_unscoped(
    session: AsyncSession, status: FeatureStatus
) -> None:
    # set_status calls get_by_id WITHOUT a project_id on an instance that may be
    # unscoped (admin). Confirm an unscoped repo can update any tenant's feature.
    repo = FeatureRepository(session)
    updated = await repo.set_status("FEAT-B1", status)
    await session.commit()
    assert updated.status == status.value
