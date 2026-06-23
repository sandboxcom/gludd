"""Unit tests for TaskEmbeddingStore (Tier 2 RAG routing — Step 3).

Covers:
- ``CANONICAL_TASK_DESCRIPTIONS`` has an entry for every ``TaskType`` member.
- ``TaskEmbeddingStore.ensure_embeddings`` seeds missing rows and embeds rows
  whose stored vector is empty (lazy read/seed), preserving existing vectors.
- ``TaskEmbeddingStore.similarity_to`` returns a cosine-similarity dict keyed
  by the other task types, with self-similarity excluded and identical
  descriptions clustering above unrelated ones.

Uses SQLite in-memory with async sessions (matches test_task_embedding_model.py).
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base, TaskEmbeddingModel
from general_ludd.schemas.benchmark import TaskType
from general_ludd.scoring.task_embeddings import (
    CANONICAL_TASK_DESCRIPTIONS,
    TaskEmbeddingStore,
)
from general_ludd.skills.embeddings import HashEmbedder


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
    session_factory = sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session


def _hash_embed(text: str, dim: int = 256) -> list[float]:
    return HashEmbedder(dim=dim).embed(text)


class TestCanonicalDescriptions:
    def test_every_task_type_has_description(self):
        for tt in TaskType:
            assert tt in CANONICAL_TASK_DESCRIPTIONS, f"missing {tt}"

    def test_descriptions_are_nonempty_strings(self):
        for tt, desc in CANONICAL_TASK_DESCRIPTIONS.items():
            assert isinstance(desc, str), f"{tt} not str"
            assert desc.strip(), f"{tt} empty"

    def test_descriptions_are_distinct(self):
        descs = list(CANONICAL_TASK_DESCRIPTIONS.values())
        assert len(set(descs)) == len(descs), "duplicate descriptions"

    def test_keys_exactly_match_task_type_members(self):
        assert set(CANONICAL_TASK_DESCRIPTIONS.keys()) == set(TaskType)


class TestEnsureEmbeddings:
    async def test_seeds_all_task_types_when_empty(self, async_session):
        store = TaskEmbeddingStore(async_session, embedder=HashEmbedder())
        await store.ensure_embeddings()

        result = await async_session.execute(select(TaskEmbeddingModel))
        rows = result.scalars().all()
        types = {r.task_type for r in rows}
        assert types == {tt.value for tt in TaskType}

    async def test_seeded_rows_have_nonzero_vectors(self, async_session):
        store = TaskEmbeddingStore(async_session, embedder=HashEmbedder())
        await store.ensure_embeddings()

        result = await async_session.execute(select(TaskEmbeddingModel))
        rows = result.scalars().all()
        for row in rows:
            vec = json.loads(row.embedding)
            assert isinstance(vec, list)
            assert len(vec) > 0
            assert row.dim == len(vec)

    async def test_embeds_rows_with_empty_vectors(self, async_session):
        async_session.add(
            TaskEmbeddingModel(
                task_type=TaskType.BUG_FIX.value,
                canonical_text="pre-existing canonical text",
                embedding="[]",
                dim=0,
            )
        )
        await async_session.flush()

        store = TaskEmbeddingStore(async_session, embedder=HashEmbedder())
        await store.ensure_embeddings()

        row = await async_session.get(TaskEmbeddingModel, TaskType.BUG_FIX.value)
        assert row is not None
        vec = json.loads(row.embedding)
        assert len(vec) > 0
        assert row.dim == len(vec)
        assert row.dim > 0

    async def test_preserves_existing_embeddings(self, async_session):
        embedder = HashEmbedder()
        existing = embedder.embed("pre-existing canonical text")
        async_session.add(
            TaskEmbeddingModel(
                task_type=TaskType.REFACTOR.value,
                canonical_text="pre-existing",
                embedding=json.dumps(existing),
                dim=len(existing),
            )
        )
        await async_session.flush()

        store = TaskEmbeddingStore(async_session, embedder=embedder)
        await store.ensure_embeddings()

        row = await async_session.get(TaskEmbeddingModel, TaskType.REFACTOR.value)
        assert row is not None
        assert json.loads(row.embedding) == existing
        assert row.dim == len(existing)

    async def test_uses_canonical_text_for_seeded_rows(self, async_session):
        store = TaskEmbeddingStore(async_session, embedder=HashEmbedder())
        await store.ensure_embeddings()

        row = await async_session.get(
            TaskEmbeddingModel, TaskType.BUG_FIX.value
        )
        assert row is not None
        assert row.canonical_text == CANONICAL_TASK_DESCRIPTIONS[TaskType.BUG_FIX]

    async def test_is_idempotent(self, async_session):
        store = TaskEmbeddingStore(async_session, embedder=HashEmbedder())
        await store.ensure_embeddings()
        result1 = await async_session.execute(select(TaskEmbeddingModel))
        embeddings_before = {
            r.task_type: r.embedding for r in result1.scalars().all()
        }

        await store.ensure_embeddings()
        result2 = await async_session.execute(select(TaskEmbeddingModel))
        rows_after = result2.scalars().all()
        embeddings_after = {r.task_type: r.embedding for r in rows_after}

        assert len(rows_after) == len(list(TaskType))
        assert embeddings_before == embeddings_after


class TestSimilarityTo:
    async def test_returns_dict_keyed_by_other_types(self, async_session):
        store = TaskEmbeddingStore(async_session, embedder=HashEmbedder())
        await store.ensure_embeddings()

        sims = await store.similarity_to(TaskType.BUG_FIX)
        assert isinstance(sims, dict)
        assert TaskType.BUG_FIX.value not in sims
        assert set(sims.keys()) == {
            tt.value for tt in TaskType if tt != TaskType.BUG_FIX
        }

    async def test_similarity_values_in_range(self, async_session):
        store = TaskEmbeddingStore(async_session, embedder=HashEmbedder())
        await store.ensure_embeddings()

        sims = await store.similarity_to(TaskType.FEATURE)
        for v in sims.values():
            assert -1.0 <= v <= 1.0

    async def test_identical_text_has_high_similarity(self, async_session):
        text = "Fix a concurrency race condition in the event loop"
        vec = _hash_embed(text)
        unrelated_text = "Write marketing copy for a astronomy newsletter"
        unrelated_vec = _hash_embed(unrelated_text)
        async_session.add_all(
            [
                TaskEmbeddingModel(
                    task_type=TaskType.BUG_FIX.value,
                    canonical_text=text,
                    embedding=json.dumps(vec),
                    dim=len(vec),
                ),
                TaskEmbeddingModel(
                    task_type=TaskType.DEBUGGING.value,
                    canonical_text=text,
                    embedding=json.dumps(vec),
                    dim=len(vec),
                ),
                TaskEmbeddingModel(
                    task_type=TaskType.DOCUMENTATION.value,
                    canonical_text=unrelated_text,
                    embedding=json.dumps(unrelated_vec),
                    dim=len(unrelated_vec),
                ),
            ]
        )
        await async_session.flush()

        store = TaskEmbeddingStore(async_session, embedder=HashEmbedder())
        sims = await store.similarity_to(TaskType.BUG_FIX)
        assert sims[TaskType.DEBUGGING.value] > sims[TaskType.DOCUMENTATION.value]
        assert sims[TaskType.DEBUGGING.value] > 0.99

    async def test_unknown_task_type_raises(self, async_session):
        store = TaskEmbeddingStore(async_session, embedder=HashEmbedder())
        await store.ensure_embeddings()

        with pytest.raises(KeyError):
            await store.similarity_to("nonexistent_task")

    async def test_query_without_embedding_raises(self, async_session):
        async_session.add(
            TaskEmbeddingModel(
                task_type=TaskType.BUG_FIX.value,
                canonical_text="no vector yet",
                embedding="[]",
                dim=0,
            )
        )
        await async_session.flush()

        store = TaskEmbeddingStore(async_session, embedder=HashEmbedder())
        with pytest.raises(KeyError):
            await store.similarity_to(TaskType.BUG_FIX)


class TestEmbedderSelection:
    def test_default_uses_hash_embedder(self, async_session):
        store = TaskEmbeddingStore(async_session)
        assert isinstance(store._embedder, HashEmbedder)

    def test_explicit_embedder_wins(self, async_session):
        custom = HashEmbedder(dim=128)
        store = TaskEmbeddingStore(async_session, embedder=custom)
        assert store._embedder is custom
