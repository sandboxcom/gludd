"""S.5: TDD pin for details=NULL on NOT NULL column guard (D1/CA-DB1).

AuditEventModel.details is ``nullable=False, default="{}"``. An explicit
``details=None`` bypasses the SQL column default and would insert NULL on
PostgreSQL. The guard ``details=details or "{}"`` in
AuditEventRepository.create() prevents this at the application layer.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from general_ludd.db.models import AuditEventModel, AuditEventType, Base, ProjectModel
from general_ludd.db.repository import AuditEventRepository


@pytest_asyncio.fixture
async def async_engine():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def async_session(async_engine):
    factory = sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as session:
        yield session


class TestS5DetailsNullGuard:
    """TDD tests pinning the details=NULL prevention guard."""

    async def test_create_without_details_defaults_to_empty_json(
        self, async_session: AsyncSession
    ) -> None:
        project = ProjectModel(name="s5-create-default")
        async_session.add(project)
        await async_session.flush()

        repo = AuditEventRepository(async_session)
        row = await repo.create(
            event_type="test",
            entity_type="todo",
            entity_id="S5-DEFAULT",
            project_id=project.project_id,
        )
        assert row.details == "{}"

    async def test_create_explicit_none_guarded_to_empty_json(
        self, async_session: AsyncSession
    ) -> None:
        project = ProjectModel(name="s5-create-none")
        async_session.add(project)
        await async_session.flush()

        repo = AuditEventRepository(async_session)
        row = await repo.create(
            event_type="test",
            entity_type="todo",
            entity_id="S5-NONE",
            project_id=project.project_id,
            details=None,
        )
        assert row.details == "{}"

    async def test_create_explicit_empty_string_guarded_to_empty_json(
        self, async_session: AsyncSession
    ) -> None:
        project = ProjectModel(name="s5-create-empty")
        async_session.add(project)
        await async_session.flush()

        repo = AuditEventRepository(async_session)
        row = await repo.create(
            event_type="test",
            entity_type="todo",
            entity_id="S5-EMPTY",
            project_id=project.project_id,
            details="",
        )
        assert row.details == "{}"

    async def test_create_valid_details_preserved(
        self, async_session: AsyncSession
    ) -> None:
        project = ProjectModel(name="s5-create-valid")
        async_session.add(project)
        await async_session.flush()

        repo = AuditEventRepository(async_session)
        row = await repo.create(
            event_type="test",
            entity_type="todo",
            entity_id="S5-VALID",
            project_id=project.project_id,
            details='{"key": "value"}',
        )
        assert row.details == '{"key": "value"}'

    async def test_record_typed_none_details_guarded_to_empty_json(
        self, async_session: AsyncSession
    ) -> None:
        project = ProjectModel(name="s5-typed-none")
        async_session.add(project)
        await async_session.flush()

        repo = AuditEventRepository(async_session)
        row = await repo.record_typed(
            event_type=AuditEventType.TODO_CREATED,
            entity_type="todo",
            entity_id="S5-RT-NONE",
            project_id=project.project_id,
            details=None,
        )
        assert row.details == "{}"

    async def test_record_typed_dict_details_serialized(
        self, async_session: AsyncSession
    ) -> None:
        project = ProjectModel(name="s5-typed-dict")
        async_session.add(project)
        await async_session.flush()

        repo = AuditEventRepository(async_session)
        row = await repo.record_typed(
            event_type=AuditEventType.TODO_CREATED,
            entity_type="todo",
            entity_id="S5-RT-DICT",
            project_id=project.project_id,
            details={"key": "value"},
        )
        assert row.details == '{"key": "value"}'

    async def test_record_typed_empty_dict_serialized_to_empty_json(
        self, async_session: AsyncSession
    ) -> None:
        project = ProjectModel(name="s5-typed-empty-dict")
        async_session.add(project)
        await async_session.flush()

        repo = AuditEventRepository(async_session)
        row = await repo.record_typed(
            event_type=AuditEventType.TODO_CREATED,
            entity_type="todo",
            entity_id="S5-RT-EMPTY",
            project_id=project.project_id,
            details={},
        )
        assert row.details == "{}"

    def test_details_column_is_not_nullable_with_default(
        self,
    ) -> None:
        col = AuditEventModel.__table__.c.details
        assert not col.nullable
        assert col.default.arg == "{}"

    async def test_create_rejects_null_project_id(
        self, async_session: AsyncSession
    ) -> None:
        repo = AuditEventRepository(async_session)
        with pytest.raises(ValueError, match="project_id is required"):
            await repo.create(
                event_type="test",
                entity_type="todo",
                entity_id="S5-NOPROJ",
                project_id=None,
            )

    async def test_orm_constructor_without_details_uses_default(
        self, async_session: AsyncSession
    ) -> None:
        """SQLAlchemy column default applied at INSERT time, not at init."""
        project = ProjectModel(name="s5-orm-default")
        async_session.add(project)
        await async_session.flush()

        ae = AuditEventModel(
            event_type="test",
            entity_type="todo",
            entity_id="S5-ORM",
            project_id=project.project_id,
        )
        async_session.add(ae)
        await async_session.flush()
        await async_session.refresh(ae)
        assert ae.details == "{}"

    async def test_create_explicit_none_guarded_persisted(
        self, async_session: AsyncSession
    ) -> None:
        """Round-trip: persist + re-read to confirm guard survives DB flush."""
        project = ProjectModel(name="s5-persist")
        async_session.add(project)
        await async_session.flush()

        repo = AuditEventRepository(async_session)
        row = await repo.create(
            event_type="test",
            entity_type="todo",
            entity_id="S5-PERSIST",
            project_id=project.project_id,
            details=None,
        )
        await async_session.flush()
        await async_session.refresh(row)
        assert row.details == "{}"
