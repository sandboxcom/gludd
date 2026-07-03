import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base, TaskDecisionModel, TaskReturnModel


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


class TestTaskDecisionModelConstraints:
    def test_return_id_has_foreign_key(self):
        fks = TaskDecisionModel.__table__.foreign_keys
        return_id_fks = [
            fk for fk in fks
            if fk.parent.name == "return_id"
        ]
        assert len(return_id_fks) >= 1, "return_id must have a foreign key constraint"
        assert return_id_fks[0].column.table.name == "task_returns"

    def test_return_id_has_unique_constraint(self):
        table = TaskDecisionModel.__table__
        found = False
        for c in table.constraints:
            if isinstance(c, sa.UniqueConstraint) and "return_id" in [col.name for col in c.columns]:
                found = True
                break
        assert found, "return_id must have a unique constraint"


class TestTaskDecisionModelIntegrity:
    @pytest.mark.asyncio
    async def test_duplicate_return_id_raises_integrity_error(self, async_session: AsyncSession):
        ret = TaskReturnModel(
            return_id="dedup-test-001",
            job_id="job-001",
            playbook="test.yml",
            queue="test-queue",
            status="completed",
        )
        async_session.add(ret)
        await async_session.flush()

        d1 = TaskDecisionModel(
            return_id="dedup-test-001",
            project_id=None,
            decision="accept",
            confidence=0.9,
        )
        async_session.add(d1)
        await async_session.flush()

        d2 = TaskDecisionModel(
            return_id="dedup-test-001",
            project_id=None,
            decision="reject",
            confidence=0.1,
        )
        async_session.add(d2)

        with pytest.raises(sa.exc.IntegrityError):
            await async_session.flush()

        await async_session.rollback()
