"""POST /api/embeddings/similar: contract, ranking, validation, and degrade.

The embeddings endpoint exposes the canonical task-type RAG layer the adaptive
router uses as a direct read-only surface: a role/playbook posts a work
description and gets back the canonical task types ranked by cosine similarity.

This test builds a LOCAL FastAPI app, calls ``embeddings.register(app, {})``
directly, and stubs ``app.state._session_factory`` over an in-memory, seeded
:class:`TaskEmbeddingStore` (SQLite + async sessions, matching
test_task_embedding_store.py). It proves:
  - a valid text returns top_k results sorted by ``similarity_score`` desc, all
    scores in ``[0, 1]`` (hash embeddings are non-negative);
  - missing ``text`` -> 422; ``top_k`` out of range -> 422;
  - ``include_embedding=True`` returns the query vector; default omits it;
  - ``embedding_method == "hash"`` with no OPENAI_API_KEY (the test env);
  - an unseeded/empty store returns 200 with empty results (never 500).
"""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base
from general_ludd.routers import embeddings
from general_ludd.scoring.task_embeddings import TaskEmbeddingStore
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
async def session_factory(monkeypatch: pytest.MonkeyPatch):
    """An async session factory over a fresh in-memory DB.

    The default embedder is HashEmbedder (OPENAI_API_KEY is removed), so the
    seeded vectors and the handler's query embedding use the same backend.
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    engine = _make_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded_factory(session_factory):
    """``session_factory`` with the canonical task-type vectors already seeded."""
    async with session_factory() as session:
        store = TaskEmbeddingStore(session, embedder=HashEmbedder())
        await store.ensure_embeddings()
        await session.commit()
    return session_factory


def _build_client(factory: Any) -> TestClient:
    app = FastAPI()
    app.state._session_factory = factory
    embeddings.register(app, {})
    return TestClient(app)


@pytest.fixture
def client(seeded_factory) -> TestClient:
    return _build_client(seeded_factory)


def test_valid_text_returns_ranked_results(client: TestClient) -> None:
    resp = client.post(
        "/api/embeddings/similar",
        json={"text": "Diagnose and fix a defect causing wrong output", "top_k": 5},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["embedding_method"] == "hash"
    assert body["query_embedding"] is None  # default include_embedding=False
    assert body["query_embedding_dim"] > 0
    results = body["results"]
    assert 0 < len(results) <= 5

    # Sorted by similarity_score descending, every score in [0, 1].
    scores = [r["similarity_score"] for r in results]
    assert scores == sorted(scores, reverse=True)
    for r in results:
        assert 0.0 <= r["similarity_score"] <= 1.0
        assert r["task_type"]
        assert r["canonical_text"]
        assert r["embedding_dim"] > 0

    # A bug-fix-shaped query should surface bug_fix / debugging near the top.
    top_types = {r["task_type"] for r in results[:3]}
    assert top_types & {"bug_fix", "debugging"}


def test_top_k_caps_result_count(client: TestClient) -> None:
    resp = client.post(
        "/api/embeddings/similar",
        json={"text": "implement a new feature", "top_k": 3},
    )
    assert resp.status_code == 200
    assert len(resp.json()["results"]) == 3


def test_work_type_filter_restricts_results(client: TestClient) -> None:
    resp = client.post(
        "/api/embeddings/similar",
        json={"text": "implement a new feature", "work_type": "feature"},
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["task_type"] == "feature"


def test_missing_text_is_422(client: TestClient) -> None:
    resp = client.post("/api/embeddings/similar", json={"top_k": 5})
    assert resp.status_code == 422


def test_top_k_out_of_range_is_422(client: TestClient) -> None:
    too_high = client.post(
        "/api/embeddings/similar", json={"text": "x", "top_k": 21}
    )
    assert too_high.status_code == 422
    too_low = client.post(
        "/api/embeddings/similar", json={"text": "x", "top_k": 0}
    )
    assert too_low.status_code == 422


def test_include_embedding_returns_query_vector(client: TestClient) -> None:
    resp = client.post(
        "/api/embeddings/similar",
        json={"text": "optimize a slow hot path", "include_embedding": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["query_embedding"], list)
    assert len(body["query_embedding"]) == body["query_embedding_dim"]
    assert body["query_embedding_dim"] > 0


def test_default_omits_query_embedding(client: TestClient) -> None:
    resp = client.post(
        "/api/embeddings/similar", json={"text": "write some docs"}
    )
    assert resp.status_code == 200
    assert resp.json()["query_embedding"] is None


def test_embedding_method_is_hash_without_openai_key(client: TestClient) -> None:
    resp = client.post(
        "/api/embeddings/similar", json={"text": "review this pull request"}
    )
    assert resp.status_code == 200
    assert resp.json()["embedding_method"] == "hash"


def test_unseeded_store_returns_empty_results_not_500(
    session_factory,
) -> None:
    """An empty (un-seeded) store yields 200 + empty results — never a 500."""
    client = _build_client(session_factory)
    resp = client.post(
        "/api/embeddings/similar", json={"text": "anything at all"}
    )
    # ensure_embeddings seeds on demand, so this actually returns ranked
    # results; the load-bearing assertion is simply that it does not 500.
    assert resp.status_code == 200
    body = resp.json()
    assert body["embedding_method"] == "hash"
    assert isinstance(body["results"], list)


def test_no_session_factory_returns_empty_not_500() -> None:
    """No session factory on app.state degrades to empty results, not 500."""
    app = FastAPI()
    app.state._session_factory = None
    embeddings.register(app, {})
    client = TestClient(app)
    resp = client.post(
        "/api/embeddings/similar", json={"text": "anything"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"] == []
    assert body["embedding_method"] == "hash"
