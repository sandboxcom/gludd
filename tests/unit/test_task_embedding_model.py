"""Unit tests for TaskEmbeddingModel (Tier 2 RAG routing substrate).

Verifies the model can be created and queried. Uses SQLite in-memory with
async sessions via aiosqlite, matching the pattern in test_db_models.py.
"""

from __future__ import annotations

from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base, TaskEmbeddingModel

assert Base is not None  # import verification


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
    session_factory = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


class TestTaskEmbeddingModelSchema:
    def test_tablename(self):
        assert TaskEmbeddingModel.__tablename__ == "task_embeddings"

    def test_has_required_fields(self):
        assert hasattr(TaskEmbeddingModel, "task_type")
        assert hasattr(TaskEmbeddingModel, "canonical_text")
        assert hasattr(TaskEmbeddingModel, "embedding")
        assert hasattr(TaskEmbeddingModel, "dim")
        assert hasattr(TaskEmbeddingModel, "updated_at")

    def test_task_type_is_primary_key(self):
        col = TaskEmbeddingModel.__table__.c.task_type
        assert col.primary_key is True

    def test_embedding_default_is_empty_json_list(self):
        col = TaskEmbeddingModel.__table__.c.embedding
        assert col.default.arg == "[]"

    def test_dim_default_is_zero(self):
        col = TaskEmbeddingModel.__table__.c.dim
        assert col.default.arg == 0


class TestTaskEmbeddingModelCRUD:
    async def test_create_task_embedding(self, async_session: AsyncSession):
        import json

        vec = [0.1, 0.2, 0.3]
        emb = TaskEmbeddingModel(
            task_type="bug_fix",
            canonical_text="Fix a concurrency race in the event loop",
            embedding=json.dumps(vec),
            dim=3,
        )
        async_session.add(emb)
        await async_session.flush()
        assert emb.task_type == "bug_fix"
        assert emb.dim == 3
        assert json.loads(emb.embedding) == vec
        assert emb.updated_at is not None

    async def test_query_task_embedding_by_pk(self, async_session: AsyncSession):
        emb = TaskEmbeddingModel(
            task_type="debugging",
            canonical_text="Diagnose and isolate a failing test",
        )
        async_session.add(emb)
        await async_session.flush()

        from sqlalchemy import select

        stmt = select(TaskEmbeddingModel).where(
            TaskEmbeddingModel.task_type == "debugging"
        )
        result = await async_session.execute(stmt)
        fetched = result.scalar_one()
        assert fetched.task_type == "debugging"
        assert fetched.canonical_text == "Diagnose and isolate a failing test"
        assert fetched.embedding == "[]"
        assert fetched.dim == 0

    async def test_query_nonexistent_returns_none(self, async_session: AsyncSession):
        from sqlalchemy import select

        stmt = select(TaskEmbeddingModel).where(
            TaskEmbeddingModel.task_type == "nonexistent_type"
        )
        result = await async_session.execute(stmt)
        fetched = result.scalar_one_or_none()
        assert fetched is None

    async def test_updated_at_auto_set_on_create(self, async_session: AsyncSession):
        emb = TaskEmbeddingModel(task_type="refactor", canonical_text="Refactor X")
        async_session.add(emb)
        await async_session.flush()
        assert emb.updated_at is not None
        assert isinstance(emb.updated_at, datetime)

    async def test_pk_collision_raises(self, async_session: AsyncSession):
        emb1 = TaskEmbeddingModel(task_type="duplicate", canonical_text="First")
        emb2 = TaskEmbeddingModel(task_type="duplicate", canonical_text="Second")
        async_session.add(emb1)
        await async_session.flush()
        async_session.expunge(emb1)
        async_session.add(emb2)
        with pytest.raises((IntegrityError, OperationalError)):
            await async_session.flush()
        assert True  # PK collision correctly raised
