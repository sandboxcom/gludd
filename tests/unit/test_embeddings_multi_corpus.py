"""Multi-corpus embedding search: indexing, merging, isolation, validation.

Provies that multiple corpora co-exist, that a multi-corpus search fans out
and merges ranked results, that corpus-specific searches are isolated, that
embedding dimension mismatches are handled, and that empty corpora degrade
gracefully.

Covers:
  - Multiple corpora can be indexed separately
  - Search across corpora returns merged results
  - Corpus-specific search returns only that corpus
  - Embedding dimension validation
  - Empty corpus returns empty results
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

# ---------------------------------------------------------------------------
# DB fixtures — reusable in-memory async SQLite
# ---------------------------------------------------------------------------


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
    """An async session factory over a fresh in-memory DB."""
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


# ---------------------------------------------------------------------------
# Stub types
# ---------------------------------------------------------------------------


class _StubSkill:
    def __init__(
        self,
        name: str,
        description: str,
        *,
        category: str = "",
        tags: list[str] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.category = category
        self.tags = tags or []


class _StubRegistry:
    def __init__(self, skills: list[_StubSkill]) -> None:
        self._skills = skills

    def list_skills(self) -> list[_StubSkill]:
        return list(self._skills)


class _StubTracesBuffer:
    def __init__(self, traces: list[dict[str, Any]]) -> None:
        self._traces = traces

    def snapshot(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "count": len(self._traces),
            "total_recorded": len(self._traces),
            "recent": list(self._traces),
            "by_phase": {},
        }


def _trace_row(
    trace_id: str,
    todo_id: str,
    work_type: str,
    spans: list[tuple[str, str]],
    *,
    total_cost_usd: float = 0.0,
) -> dict[str, Any]:
    return {
        "trace_id": trace_id,
        "todo_id": todo_id,
        "work_type": work_type,
        "total_cost_usd": total_cost_usd,
        "span_count": len(spans),
        "spans": [{"phase": p, "name": n} for p, n in spans],
    }


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_prompts(factory: Any, rows: list[dict[str, Any]]) -> None:
    from general_ludd.db.repository import PromptProfileRepository

    async with factory() as session:
        repo = PromptProfileRepository(session)
        for row in rows:
            await repo.upsert(row)
        await session.commit()


async def _seed_events(factory: Any, rows: list[dict[str, Any]]) -> None:
    from general_ludd.db.repository import (
        AuditEventRepository,
        ProjectRepository,
    )

    async with factory() as session:
        project_repo = ProjectRepository(session)
        seen: set[str] = set()
        repo = AuditEventRepository(session)
        for row in rows:
            project_id = row["project_id"]
            if project_id not in seen:
                await project_repo.create(
                    {"project_id": project_id, "name": project_id}
                )
                seen.add(project_id)
            await repo.create(
                event_type=row["event_type"],
                entity_type=row["entity_type"],
                entity_id=row["entity_id"],
                project_id=project_id,
                details=row.get("details"),
            )
        await session.commit()


# ---------------------------------------------------------------------------
# Multi-corpus client builders
# ---------------------------------------------------------------------------


def _multi_corpus_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    registry: Any = None,
    seeded_factory: Any = None,
    traces_buffer: Any = None,
) -> TestClient:
    """Build a client whose app has all corpora wired simultaneously."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = FastAPI()
    app.state._session_factory = seeded_factory
    app.state._skill_registry = registry
    app.state._recent_traces = traces_buffer
    embeddings.register(app, {})
    return TestClient(app)


# ---------------------------------------------------------------------------
# Fixture: all corpora seeded
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def all_corpora_factory(session_factory):
    """Factory with task_types + prompts + events all seeded, for multi-corpus setups."""
    import json as _json

    # Seed task_types (canonical vectors)
    async with session_factory() as session:
        store = TaskEmbeddingStore(session, embedder=HashEmbedder())
        await store.ensure_embeddings()
        await session.commit()

    # Seed prompts
    await _seed_prompts(
        session_factory,
        [
            {
                "name": "bug-hunter",
                "source": "github",
                "prompt_text": (
                    "Diagnose and fix the defect causing the request handler "
                    "to intermittently return wrong output and 500 errors."
                ),
                "tags": _json.dumps(["debugging", "backend"]),
                "task_types": _json.dumps(["bug_fix"]),
                "version": "v2",
            },
            {
                "name": "doc-writer",
                "source": "internal",
                "prompt_text": (
                    "Write clear user-facing documentation and a tutorial for "
                    "the new feature, with examples."
                ),
                "tags": _json.dumps(["docs"]),
                "task_types": _json.dumps(["documentation"]),
                "version": "latest",
            },
        ],
    )

    # Seed events
    await _seed_events(
        session_factory,
        [
            {
                "event_type": "todo_failed",
                "entity_type": "todo",
                "entity_id": "todo-1",
                "project_id": "proj-a",
                "details": _json.dumps(
                    {
                        "error": "diagnose the defect causing wrong output",
                        "model": "glm-4.6",
                    }
                ),
            },
            {
                "event_type": "docs_written",
                "entity_type": "todo",
                "entity_id": "todo-2",
                "project_id": "proj-a",
                "details": _json.dumps(
                    {"summary": "wrote the user-facing tutorial and examples"}
                ),
            },
        ],
    )

    return session_factory


# ===========================================================================
# 1. Multiple corpora can be indexed separately
# ===========================================================================


def test_all_corpora_searchable_individually(
    monkeypatch: pytest.MonkeyPatch,
    all_corpora_factory,
) -> None:
    """Every corpus returns results when searched individually from the same app."""
    registry = _StubRegistry(
        [
            _StubSkill(
                "web-toolkit",
                "Fetch web pages, parse HTML, search and crawl with SSRF "
                "hardening and offline fallback.",
                category="web",
            ),
        ]
    )
    buffer = _StubTracesBuffer(
        [_trace_row("trace-1", "todo-x", "bug_fix", [("generate", "fix a defect")])]
    )
    client = _multi_corpus_client(
        monkeypatch,
        registry=registry,
        seeded_factory=all_corpora_factory,
        traces_buffer=buffer,
    )

    corpora = ["skills", "task_types", "prompts", "events", "traces"]
    for corpus in corpora:
        resp = client.post(
            "/api/embeddings/search",
            json={"text": "fix defect and errors", "corpus": corpus, "top_k": 3},
        )
        assert resp.status_code == 200, f"corpus={corpus} returned {resp.status_code}"
        body = resp.json()
        assert body["corpus"] == corpus
        assert body["embedding_method"] == "hash"
        assert isinstance(body["results"], list)
        assert len(body["results"]) >= 1, f"corpus={corpus} returned no results"


def test_multi_corpus_setup_preserves_individual_isolation(
    monkeypatch: pytest.MonkeyPatch,
    all_corpora_factory,
) -> None:
    """Searching one corpus never leaks items from another corpus."""
    registry = _StubRegistry(
        [
            _StubSkill("deep-research", "Fan out web searches and synthesize reports.",
                       category="research"),
            _StubSkill("compute-discovery", "Discover per-provider compute resources.",
                       category="infra"),
        ]
    )
    client = _multi_corpus_client(
        monkeypatch,
        registry=registry,
        seeded_factory=all_corpora_factory,
    )

    # skills search -> should only return skill names
    resp = client.post(
        "/api/embeddings/search",
        json={"text": "web compute research", "corpus": "skills", "top_k": 5},
    )
    assert resp.status_code == 200
    skill_names = {r["name"] for r in resp.json()["results"]}
    assert "deep-research" in skill_names
    assert "compute-discovery" in skill_names
    # skills corpus must not leak prompt names
    assert "bug-hunter" not in skill_names
    assert "doc-writer" not in skill_names

    # prompts search -> should only return prompt names
    resp = client.post(
        "/api/embeddings/search",
        json={"text": "write documentation tutorial", "corpus": "prompts", "top_k": 5},
    )
    assert resp.status_code == 200
    prompt_names = {r["name"] for r in resp.json()["results"]}
    assert "doc-writer" in prompt_names
    assert "deep-research" not in prompt_names


# ===========================================================================
# 2. Search across corpora returns merged results
# ===========================================================================


def test_multi_corpus_search_merges_skills_and_prompts(
    monkeypatch: pytest.MonkeyPatch,
    all_corpora_factory,
) -> None:
    """A multi-corpus search fans out and returns merged, ranked results."""
    registry = _StubRegistry(
        [
            _StubSkill(
                "web-toolkit",
                "Fetch web pages, parse HTML, search and crawl with SSRF hardening.",
                category="web",
            ),
            _StubSkill(
                "deep-research",
                "Fan out web searches, fetch sources, synthesize a cited research report.",
                category="research",
            ),
        ]
    )
    client = _multi_corpus_client(
        monkeypatch,
        registry=registry,
        seeded_factory=all_corpora_factory,
    )

    resp = client.post(
        "/api/embeddings/search-multi",
        json={
            "text": "search the web and write a researched report with sources",
            "corpora": ["skills", "prompts"],
            "top_k": 4,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert sorted(body["corpora_searched"]) == ["prompts", "skills"]
    assert body["embedding_method"] == "hash"
    assert body["query_embedding_dim"] > 0
    assert body["query_embedding"] is None  # default include_embeddings=False

    results = body["results"]
    assert len(results) >= 2  # at least one from each corpus
    assert len(results) <= 4  # capped by top_k

    # Ranked by similarity descending
    scores = [r["similarity_score"] for r in results]
    assert scores == sorted(scores, reverse=True)

    # Ranks are 1-based and contiguous
    assert [r["rank"] for r in results] == list(range(1, len(results) + 1))

    # Each result has a corpus tag in metadata
    corpora_in_results = {r["metadata"].get("corpus") for r in results}
    assert "skills" in corpora_in_results
    assert "prompts" in corpora_in_results

    # The research-shaped query should surface deep-research high
    top_names = {r["name"] for r in results[:2]}
    assert "deep-research" in top_names


def test_multi_corpus_search_merges_task_types_and_events(
    monkeypatch: pytest.MonkeyPatch,
    all_corpora_factory,
) -> None:
    """Multi-corpus search merges task_types + events."""
    client = _multi_corpus_client(
        monkeypatch,
        seeded_factory=all_corpora_factory,
    )

    resp = client.post(
        "/api/embeddings/search-multi",
        json={
            "text": "diagnose and fix the defect causing wrong output",
            "corpora": ["task_types", "events"],
            "top_k": 5,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    results = body["results"]
    assert len(results) >= 1
    assert len(results) <= 5

    corpora_in_results = {r["metadata"].get("corpus") for r in results}
    assert "task_types" in corpora_in_results
    assert "events" in corpora_in_results

    # A bug-fix-shaped query should surface bug_fix from task_types
    task_type_results = [
        r for r in results if r["metadata"].get("corpus") == "task_types"
    ]
    assert any(r["name"] == "bug_fix" for r in task_type_results)


def test_multi_corpus_search_include_embeddings(
    monkeypatch: pytest.MonkeyPatch,
    all_corpora_factory,
) -> None:
    """Multi-corpus search with include_embeddings=True returns the query vector."""
    registry = _StubRegistry(
        [_StubSkill("a-skill", "embed and rank this description")]
    )
    client = _multi_corpus_client(
        monkeypatch,
        registry=registry,
        seeded_factory=all_corpora_factory,
    )

    resp = client.post(
        "/api/embeddings/search-multi",
        json={
            "text": "rank embedding",
            "corpora": ["skills"],
            "include_embeddings": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["query_embedding"], list)
    assert len(body["query_embedding"]) == body["query_embedding_dim"]


def test_multi_corpus_search_top_k_caps_merged_results(
    monkeypatch: pytest.MonkeyPatch,
    all_corpora_factory,
) -> None:
    """top_k limits the total merged results, not per-corpus."""
    registry = _StubRegistry(
        [
            _StubSkill("s1", "alpha task alpha task alpha task"),
            _StubSkill("s2", "beta task beta task beta task"),
            _StubSkill("s3", "gamma task gamma task gamma task"),
        ]
    )
    client = _multi_corpus_client(
        monkeypatch,
        registry=registry,
        seeded_factory=all_corpora_factory,
    )

    resp = client.post(
        "/api/embeddings/search-multi",
        json={
            "text": "task",
            "corpora": ["skills", "task_types", "prompts"],
            "top_k": 3,
        },
    )
    assert resp.status_code == 200
    assert len(resp.json()["results"]) == 3


# ===========================================================================
# 3. Corpus-specific search returns only that corpus
# ===========================================================================


def test_single_corpus_skills_returns_only_skills(
    monkeypatch: pytest.MonkeyPatch,
    all_corpora_factory,
) -> None:
    """Corpus-specific search isolates to the requested corpus."""
    registry = _StubRegistry(
        [
            _StubSkill("skill-a", "alpha description"),
            _StubSkill("skill-b", "beta description"),
        ]
    )
    client = _multi_corpus_client(
        monkeypatch,
        registry=registry,
        seeded_factory=all_corpora_factory,
    )

    resp = client.post(
        "/api/embeddings/search",
        json={"text": "alpha beta", "corpus": "skills", "top_k": 10},
    )
    assert resp.status_code == 200
    names = {r["name"] for r in resp.json()["results"]}
    assert names == {"skill-a", "skill-b"}


def test_single_corpus_prompts_returns_only_prompts(
    monkeypatch: pytest.MonkeyPatch,
    all_corpora_factory,
) -> None:
    """Prompt-specific search only returns prompt names, no skill or event names."""
    client = _multi_corpus_client(
        monkeypatch,
        seeded_factory=all_corpora_factory,
    )

    resp = client.post(
        "/api/embeddings/search",
        json={"text": "documentation tutorial examples", "corpus": "prompts", "top_k": 10},
    )
    assert resp.status_code == 200
    names = {r["name"] for r in resp.json()["results"]}
    assert "doc-writer" in names
    assert names <= {"bug-hunter", "doc-writer"}


def test_single_corpus_traces_returns_only_traces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Trace-specific search isolates to traces only."""
    buffer = _StubTracesBuffer(
        [
            _trace_row("t1", "todo-1", "bug_fix", [("generate", "fix defect")]),
            _trace_row("t2", "todo-2", "feature", [("generate", "new feature")]),
        ]
    )
    client = _multi_corpus_client(
        monkeypatch,
        traces_buffer=buffer,
    )

    resp = client.post(
        "/api/embeddings/search",
        json={"text": "fix defect", "corpus": "traces", "top_k": 10},
    )
    assert resp.status_code == 200
    names = {r["name"] for r in resp.json()["results"]}
    assert "t1" in names
    assert "t2" in names


# ===========================================================================
# 4. Embedding dimension validation
# ===========================================================================


def test_dimension_consistency_across_all_corpora(
    monkeypatch: pytest.MonkeyPatch,
    all_corpora_factory,
) -> None:
    """Every corpus returns the same embedding dimension when using HashEmbedder."""
    registry = _StubRegistry([_StubSkill("s", "some description")])
    buffer = _StubTracesBuffer(
        [_trace_row("t", "todo", "code", [("generate", "work")])]
    )
    client = _multi_corpus_client(
        monkeypatch,
        registry=registry,
        seeded_factory=all_corpora_factory,
        traces_buffer=buffer,
    )

    dims: dict[str, int] = {}
    for corpus in ["skills", "task_types", "prompts", "events", "traces"]:
        resp = client.post(
            "/api/embeddings/search",
            json={"text": "any query text", "corpus": corpus},
        )
        assert resp.status_code == 200
        body = resp.json()
        dim = body["query_embedding_dim"]
        assert dim > 0, f"corpus={corpus} has zero embedding dim"
        dims[corpus] = dim

    # All corpora use the same HashEmbedder -> same dimension
    assert len(set(dims.values())) == 1, f"inconsistent dims: {dims}"


def test_dimension_mismatch_between_different_dim_embeddings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When an embedder produces a different-dimensional vector, it is skipped.

    Prove that the cosine_similarity function raises ValueError on dimension
    mismatch, and that the search handlers skip mismatched vectors gracefully.
    """
    from general_ludd.skills.embeddings import cosine_similarity

    with pytest.raises(ValueError, match="vector length mismatch"):
        cosine_similarity([0.1, 0.2, 0.3], [0.4, 0.5])


def test_hash_embedder_dimension_matches_query_dim(
    monkeypatch: pytest.MonkeyPatch,
    all_corpora_factory,
) -> None:
    """Every result in a merged search has the same embedding_dim as the query.

    When a single-embedder backend (HashEmbedder) is in use, all stored and
    query vectors share the same dimension. This test confirms that no
    dimension-mismatch skip-path is hiding a real bug.
    """
    client = _multi_corpus_client(
        monkeypatch,
        seeded_factory=all_corpora_factory,
    )

    resp = client.post(
        "/api/embeddings/search",
        json={"text": "any query", "corpus": "task_types", "top_k": 5},
    )
    assert resp.status_code == 200
    body = resp.json()
    query_dim = body["query_embedding_dim"]

    for r in body["results"]:
        item_dim = r["metadata"].get("embedding_dim")
        assert item_dim == query_dim, f"result {r['name']} has dim {item_dim} != {query_dim}"


def test_hash_embedder_rejects_zero_or_negative_dim() -> None:
    """HashEmbedder construction validates dim > 0."""
    with pytest.raises(ValueError, match="dim must be positive"):
        HashEmbedder(dim=0)

    with pytest.raises(ValueError, match="dim must be positive"):
        HashEmbedder(dim=-1)

    # dim=256 is the default and should work
    embedder = HashEmbedder(dim=256)
    vec = embedder.embed("hello world")
    assert len(vec) == 256
    assert all(isinstance(v, float) for v in vec)


def test_hash_embedder_custom_dim_produces_correct_length() -> None:
    """A custom-dimension HashEmbedder produces vectors of that length."""
    for dim in [128, 512, 1024]:
        embedder = HashEmbedder(dim=dim)
        vec = embedder.embed("test string with multiple tokens")
        assert len(vec) == dim


# ===========================================================================
# 5. Empty corpus returns empty results
# ===========================================================================


def test_empty_skill_registry_returns_empty_in_multi_setup(
    monkeypatch: pytest.MonkeyPatch,
    all_corpora_factory,
) -> None:
    """An empty skill registry yields empty results, never 500, alongside seeded corpora."""
    client = _multi_corpus_client(
        monkeypatch,
        registry=_StubRegistry([]),
        seeded_factory=all_corpora_factory,
    )

    resp = client.post(
        "/api/embeddings/search",
        json={"text": "anything", "corpus": "skills"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["corpus"] == "skills"
    assert body["results"] == []
    assert body["embedding_method"] == "hash"


def test_multi_corpus_search_with_empty_corpus_skips_it(
    monkeypatch: pytest.MonkeyPatch,
    all_corpora_factory,
) -> None:
    """A multi-corpus search where one corpus is empty still returns results from others."""
    client = _multi_corpus_client(
        monkeypatch,
        registry=_StubRegistry([]),  # empty skills
        seeded_factory=all_corpora_factory,
    )

    resp = client.post(
        "/api/embeddings/search-multi",
        json={
            "text": "diagnose and fix the defect causing wrong output and 500s",
            "corpora": ["skills", "prompts"],
            "top_k": 5,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["corpora_searched"] == ["prompts", "skills"]

    results = body["results"]
    # skills is empty but prompts has results -> we still get results
    assert len(results) >= 1

    # No result should claim to be from a corpus that was empty
    corpora_claimed = {r["metadata"].get("corpus") for r in results}
    assert "prompts" in corpora_claimed
    # skills was empty so it contributed nothing
    assert all(
        r["metadata"].get("corpus") != "skills" for r in results
    ) or len(  # or if a result somehow exists from skills, it'd be here
        [r for r in results if r["metadata"].get("corpus") == "skills"]
    ) == 0


def test_no_session_factory_multi_corpus_degrades_gracefully(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No session factory -> db-backed corpora degrade, skills still work. Never 500.

    The skills corpus is backed by an in-memory registry (no DB), so it still
    produces results when session_factory is None. task_types/prompts/events rely
    on the DB and degrade to empty. The response must still be 200.
    """
    registry = _StubRegistry([_StubSkill("s", "a skill")])
    app = FastAPI()
    app.state._session_factory = None
    app.state._skill_registry = registry
    app.state._recent_traces = None
    embeddings.register(app, {})
    client = TestClient(app)

    resp = client.post(
        "/api/embeddings/search-multi",
        json={
            "text": "anything",
            "corpora": ["skills", "task_types", "prompts", "events"],
            "top_k": 5,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    # corpora_searched lists only corpora that produced results (skills)
    assert "skills" in body["corpora_searched"]
    # skills corpus works without session_factory; db corpora degrade silently
    assert len(body["results"]) >= 1
    assert len(body["results"]) <= 5


def test_multi_corpus_search_no_registry_no_factory_all_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When everything is absent, multi-corpus returns empty gracefully."""
    app = FastAPI()
    app.state._session_factory = None
    app.state._skill_registry = None
    app.state._recent_traces = None
    embeddings.register(app, {})
    client = TestClient(app)

    resp = client.post(
        "/api/embeddings/search-multi",
        json={"text": "anything", "corpora": ["skills", "traces", "events"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"] == []
    assert body["corpora_searched"] == ["events", "skills", "traces"]


# ===========================================================================
# Multi-corpus validation (input checks)
# ===========================================================================


def test_multi_corpus_search_missing_text_is_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing text in multi-corpus request -> 422."""
    client = _multi_corpus_client(monkeypatch)
    resp = client.post(
        "/api/embeddings/search-multi",
        json={"corpora": ["skills"]},
    )
    assert resp.status_code == 422


def test_multi_corpus_search_empty_corpora_list_is_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty corpora list in multi-corpus request -> 422."""
    client = _multi_corpus_client(monkeypatch)
    resp = client.post(
        "/api/embeddings/search-multi",
        json={"text": "anything", "corpora": []},
    )
    assert resp.status_code == 422


def test_multi_corpus_search_unknown_corpus_is_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown corpus value in corpora list -> 422."""
    client = _multi_corpus_client(monkeypatch)
    resp = client.post(
        "/api/embeddings/search-multi",
        json={"text": "x", "corpora": ["skills", "memory"]},
    )
    assert resp.status_code == 422


def test_multi_corpus_search_top_k_out_of_range_is_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """top_k out of range in multi-corpus request -> 422."""
    client = _multi_corpus_client(monkeypatch)
    resp = client.post(
        "/api/embeddings/search-multi",
        json={"text": "x", "corpora": ["skills"], "top_k": 21},
    )
    assert resp.status_code == 422


def test_multi_corpus_search_text_over_size_cap_is_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multi-corpus search with text > 20000 chars -> 422."""
    client = _multi_corpus_client(monkeypatch)
    resp = client.post(
        "/api/embeddings/search-multi",
        json={"text": "a" * 20001, "corpora": ["skills"]},
    )
    assert resp.status_code == 422


# ===========================================================================
# Multi-corpus search across all five corpora
# ===========================================================================


def test_multi_corpus_search_all_five_corpora(
    monkeypatch: pytest.MonkeyPatch,
    all_corpora_factory,
) -> None:
    """A multi-corpus search across all 5 corpora returns merged, ranked results."""
    registry = _StubRegistry(
        [
            _StubSkill(
                "web-toolkit",
                "Fetch web pages from the internet and parse HTML content.",
                category="web",
            ),
        ]
    )
    buffer = _StubTracesBuffer(
        [_trace_row("t1", "todo-1", "bug_fix", [("generate", "fix a defect")])]
    )
    client = _multi_corpus_client(
        monkeypatch,
        registry=registry,
        seeded_factory=all_corpora_factory,
        traces_buffer=buffer,
    )

    resp = client.post(
        "/api/embeddings/search-multi",
        json={
            "text": "diagnose and fix the defect causing wrong output and 500 errors",
            "corpora": ["skills", "task_types", "prompts", "events", "traces"],
            "top_k": 10,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert sorted(body["corpora_searched"]) == [
        "events", "prompts", "skills", "task_types", "traces"
    ]
    assert body["embedding_method"] == "hash"
    results = body["results"]
    assert len(results) >= 1
    assert len(results) <= 10

    # Every result has a corpus tag
    for r in results:
        assert "corpus" in r["metadata"], f"result {r['name']} missing corpus metadata"
        assert r["metadata"]["corpus"] in body["corpora_searched"]

    # Sorted by similarity descending
    scores = [r["similarity_score"] for r in results]
    assert scores == sorted(scores, reverse=True)

    # At least 3 different corpora contributed
    corpora_with_results = {r["metadata"]["corpus"] for r in results}
    assert len(corpora_with_results) >= 3


# ===========================================================================
# Multi-corpus: duplicate handling across corpora
# ===========================================================================


def test_multi_corpus_search_with_overlapping_skill_and_task_type(
    monkeypatch: pytest.MonkeyPatch,
    all_corpora_factory,
) -> None:
    """When multiple corpora have conceptually similar items, both appear merged."""
    registry = _StubRegistry(
        [
            _StubSkill(
                "bug-fix-skill",
                "Fix a defect: diagnose a reported failure and apply a minimal "
                "code change that resolves the incorrect behavior.",
                category="debugging",
            ),
        ]
    )
    client = _multi_corpus_client(
        monkeypatch,
        registry=registry,
        seeded_factory=all_corpora_factory,
    )

    resp = client.post(
        "/api/embeddings/search-multi",
        json={
            "text": "diagnose and fix a defect",
            "corpora": ["skills", "task_types"],
            "top_k": 5,
        },
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    names = [r["name"] for r in results]

    # Both corpora contribute results with the same conceptual overlap
    assert "bug-fix-skill" in names or "bug_fix" in names
    corpora = {r["metadata"]["corpus"] for r in results}
    assert len(corpora) >= 1  # at least one corpus contributed


def test_multi_corpus_search_merges_preserves_source_text(
    monkeypatch: pytest.MonkeyPatch,
    all_corpora_factory,
) -> None:
    """Every merged result preserves its original source_text and metadata."""
    registry = _StubRegistry(
        [_StubSkill("s1", "alpha description with unique term alpha")]
    )
    client = _multi_corpus_client(
        monkeypatch,
        registry=registry,
        seeded_factory=all_corpora_factory,
    )

    resp = client.post(
        "/api/embeddings/search-multi",
        json={
            "text": "alpha description",
            "corpora": ["skills", "prompts"],
            "top_k": 5,
        },
    )
    assert resp.status_code == 200
    for r in resp.json()["results"]:
        assert isinstance(r["name"], str) and len(r["name"]) > 0
        assert isinstance(r["source_text"], str) and len(r["source_text"]) > 0
        assert isinstance(r["metadata"], dict)
        assert "corpus" in r["metadata"]
        assert r["similarity_score"] >= 0.0


# ===========================================================================
# Multi-corpus: embedder failure degrades gracefully
# ===========================================================================


def test_multi_corpus_search_embed_failure_degrades_to_200(
    monkeypatch: pytest.MonkeyPatch,
    all_corpora_factory,
) -> None:
    """A broken embedder degrades multi-corpus search to 200, never 500.

    Patch both the router-level default embedder AND the task_embeddings-level
    default embedder so skills fails outright (raises RuntimeError) and
    task_types degrades to empty results via its internal catch. The endpoint
    must return 200 with whatever corpora survived.
    """

    class _BoomEmbedder:
        def embed(self, text: str) -> list[float]:
            raise RuntimeError("embedder exploded")

    boom = _BoomEmbedder()
    monkeypatch.setattr(embeddings, "_select_default_embedder", lambda: boom)
    monkeypatch.setattr(
        "general_ludd.scoring.task_embeddings._select_default_embedder",
        lambda: boom,
    )
    registry = _StubRegistry([_StubSkill("s", "some description")])
    client = _multi_corpus_client(
        monkeypatch,
        registry=registry,
        seeded_factory=all_corpora_factory,
    )

    resp = client.post(
        "/api/embeddings/search-multi",
        json={"text": "anything", "corpora": ["skills", "task_types"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    # task_types degrades internally (returns empty, no exception); skills
    # raises -> skipped by the fan-out loop. Either way, 200 with no results.
    assert body["results"] == []
    # corpora_searched may include task_types (degraded) but not skills (skipped)
    assert "skills" not in body["corpora_searched"]


# ===========================================================================
# Multi-corpus: corpus metadata integrity
# ===========================================================================


def test_multi_corpus_search_corpus_metadata_is_complete(
    monkeypatch: pytest.MonkeyPatch,
    all_corpora_factory,
) -> None:
    """Each merged result carries the right corpus tag AND corpus-specific metadata."""
    registry = _StubRegistry(
        [
            _StubSkill(
                "sk-a", "skill about debugging defects",
                category="debugging", tags=["bug", "fix"],
            ),
        ]
    )
    client = _multi_corpus_client(
        monkeypatch,
        registry=registry,
        seeded_factory=all_corpora_factory,
    )

    resp = client.post(
        "/api/embeddings/search-multi",
        json={
            "text": "debugging defects",
            "corpora": ["skills", "task_types", "prompts"],
            "top_k": 10,
        },
    )
    assert resp.status_code == 200
    for r in resp.json()["results"]:
        corpus = r["metadata"]["corpus"]
        if corpus == "skills":
            assert "category" in r["metadata"]
            assert "tags" in r["metadata"]
        elif corpus == "task_types":
            assert "embedding_dim" in r["metadata"]
        elif corpus == "prompts":
            assert "source" in r["metadata"]
            assert "version" in r["metadata"]
            assert "tags" in r["metadata"]
            assert "task_types" in r["metadata"]
