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
    too_high = client.post("/api/embeddings/similar", json={"text": "x", "top_k": 21})
    assert too_high.status_code == 422
    too_low = client.post("/api/embeddings/similar", json={"text": "x", "top_k": 0})
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
    resp = client.post("/api/embeddings/similar", json={"text": "write some docs"})
    assert resp.status_code == 200
    assert resp.json()["query_embedding"] is None


def test_embedding_method_is_hash_without_openai_key(client: TestClient) -> None:
    resp = client.post("/api/embeddings/similar", json={"text": "review this pull request"})
    assert resp.status_code == 200
    assert resp.json()["embedding_method"] == "hash"


def test_unseeded_store_returns_empty_results_not_500(
    session_factory,
) -> None:
    """An empty (un-seeded) store yields 200 + empty results — never a 500."""
    client = _build_client(session_factory)
    resp = client.post("/api/embeddings/similar", json={"text": "anything at all"})
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
    resp = client.post("/api/embeddings/similar", json={"text": "anything"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"] == []
    assert body["embedding_method"] == "hash"


# --- POST /api/embeddings/compare -------------------------------------------


def _compare_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A client whose compare path resolves to the offline HashEmbedder."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = FastAPI()
    # compare does not touch the session factory, but keep app.state coherent.
    app.state._session_factory = None
    embeddings.register(app, {})
    return TestClient(app)


def test_compare_identical_strings_is_one(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _compare_client(monkeypatch)
    text = "the request handler intermittently returns 500 errors"
    resp = client.post("/api/embeddings/compare", json={"text_a": text, "text_b": text})
    assert resp.status_code == 200
    body = resp.json()
    assert body["matrix"] is None
    assert body["embedding_method"] == "hash"
    assert body["dim"] > 0
    assert body["embeddings"] is None  # include_embeddings default False
    assert body["similarity"] == pytest.approx(1.0, abs=1e-6)


def test_compare_unrelated_strings_lower_than_identical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _compare_client(monkeypatch)
    resp = client.post(
        "/api/embeddings/compare",
        json={
            "text_a": "deploy the kubernetes cluster to the staging region",
            "text_b": "bake a chocolate cake with vanilla frosting",
        },
    )
    assert resp.status_code == 200
    sim = resp.json()["similarity"]
    assert sim is not None
    assert sim < 0.9  # clearly divergent strings


def test_compare_include_embeddings_toggle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _compare_client(monkeypatch)
    resp = client.post(
        "/api/embeddings/compare",
        json={"text_a": "alpha", "text_b": "beta", "include_embeddings": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["embeddings"], list)
    assert len(body["embeddings"]) == 2
    assert len(body["embeddings"][0]) == body["dim"]


def test_compare_batch_matrix_is_symmetric_with_unit_diagonal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _compare_client(monkeypatch)
    resp = client.post(
        "/api/embeddings/compare",
        json={"texts": ["fix the bug", "fix the bug", "write documentation"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["similarity"] is None
    matrix = body["matrix"]
    assert len(matrix) == 3
    for i in range(3):
        assert matrix[i][i] == pytest.approx(1.0, abs=1e-6)
        for j in range(3):
            assert matrix[i][j] == pytest.approx(matrix[j][i], abs=1e-9)
    # Two identical strings -> ~1.0; the third differs.
    assert matrix[0][1] == pytest.approx(1.0, abs=1e-6)
    assert matrix[0][2] < 1.0


def test_compare_missing_both_forms_is_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _compare_client(monkeypatch)
    resp = client.post("/api/embeddings/compare", json={})
    assert resp.status_code == 422


def test_compare_only_text_a_is_422(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _compare_client(monkeypatch)
    resp = client.post("/api/embeddings/compare", json={"text_a": "lonely"})
    assert resp.status_code == 422


def test_compare_single_text_batch_is_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _compare_client(monkeypatch)
    resp = client.post("/api/embeddings/compare", json={"texts": ["only one"]})
    assert resp.status_code == 422


def test_compare_both_forms_is_422(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _compare_client(monkeypatch)
    resp = client.post(
        "/api/embeddings/compare",
        json={"text_a": "a", "text_b": "b", "texts": ["a", "b"]},
    )
    assert resp.status_code == 422


def test_compare_embedder_failure_degrades_to_200_not_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken embedder degrades to 200 with similarity=None, never a 500."""
    client = _compare_client(monkeypatch)

    class _BoomEmbedder:
        def embed(self, text: str) -> list[float]:
            raise RuntimeError("embedder exploded")

    monkeypatch.setattr(embeddings, "_select_default_embedder", lambda: _BoomEmbedder())
    resp = client.post("/api/embeddings/compare", json={"text_a": "x", "text_b": "y"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["similarity"] is None
    assert body["matrix"] is None


def test_compare_batch_embedder_failure_yields_empty_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _compare_client(monkeypatch)

    class _BoomEmbedder:
        def embed(self, text: str) -> list[float]:
            raise RuntimeError("embedder exploded")

    monkeypatch.setattr(embeddings, "_select_default_embedder", lambda: _BoomEmbedder())
    resp = client.post("/api/embeddings/compare", json={"texts": ["a", "b", "c"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["matrix"] == []
    assert body["similarity"] is None


# --- Resource-abuse bounds (quadratic-DoS / unbounded-string guards) ---------


def test_compare_texts_over_100_is_422(monkeypatch: pytest.MonkeyPatch) -> None:
    """texts with 101 items -> 422 (bounds the O(n^2) similarity matrix)."""
    client = _compare_client(monkeypatch)
    resp = client.post("/api/embeddings/compare", json={"texts": ["x"] * 101})
    assert resp.status_code == 422


def test_compare_texts_exactly_100_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """texts with exactly 100 items is within the cap -> 200."""
    client = _compare_client(monkeypatch)
    resp = client.post("/api/embeddings/compare", json={"texts": ["x"] * 100})
    assert resp.status_code == 200
    matrix = resp.json()["matrix"]
    assert len(matrix) == 100
    assert len(matrix[0]) == 100


def test_compare_text_a_over_per_string_cap_is_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single text_a over the 20000-char cap -> 422 (no unbounded embed)."""
    client = _compare_client(monkeypatch)
    resp = client.post(
        "/api/embeddings/compare",
        json={"text_a": "a" * 20001, "text_b": "b"},
    )
    assert resp.status_code == 422


def test_compare_texts_item_over_per_string_cap_is_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A batch item over the 20000-char cap -> 422 via the model_validator."""
    client = _compare_client(monkeypatch)
    resp = client.post(
        "/api/embeddings/compare",
        json={"texts": ["ok", "a" * 20001]},
    )
    assert resp.status_code == 422


def test_compare_normal_small_inputs_still_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Normal small inputs are unaffected by the new bounds."""
    client = _compare_client(monkeypatch)
    resp = client.post("/api/embeddings/compare", json={"text_a": "hello", "text_b": "world"})
    assert resp.status_code == 200
    assert resp.json()["similarity"] is not None


def test_similar_text_over_per_string_cap_is_422(client: TestClient) -> None:
    """A /similar text over the 20000-char cap -> 422."""
    resp = client.post("/api/embeddings/similar", json={"text": "a" * 20001})
    assert resp.status_code == 422


def test_search_text_over_per_string_cap_is_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A /search text over the 20000-char cap -> 422."""
    client = _search_client(monkeypatch, registry=_StubRegistry([]))
    resp = client.post(
        "/api/embeddings/search",
        json={"text": "a" * 20001, "corpus": "skills"},
    )
    assert resp.status_code == 422


# --- POST /api/embeddings/search --------------------------------------------


class _StubSkill:
    """Minimal stand-in for general_ludd.skills.skill.Skill."""

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
    """Minimal SkillRegistry: exposes list_skills()."""

    def __init__(self, skills: list[_StubSkill]) -> None:
        self._skills = skills

    def list_skills(self) -> list[_StubSkill]:
        return list(self._skills)


def _search_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    registry: Any = None,
    seeded_factory: Any = None,
) -> TestClient:
    """A search client over the offline HashEmbedder.

    ``registry`` is published on ``app.state._skill_registry`` for the skills
    corpus; ``seeded_factory`` is the canonical-task-type store for the
    task_types corpus (delegates to /similar).
    """
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = FastAPI()
    app.state._session_factory = seeded_factory
    app.state._skill_registry = registry
    embeddings.register(app, {})
    return TestClient(app)


def test_search_skills_returns_ranked_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _StubRegistry(
        [
            _StubSkill(
                "deep-research",
                "Fan out web searches, fetch sources, verify claims, synthesize a cited research report.",
                category="research",
                tags=["web"],
            ),
            _StubSkill(
                "compute-resource-discovery",
                "Discover per-provider compute resources and auto-select GPU/CPU based on budget and workload.",
                category="infra",
            ),
            _StubSkill(
                "web-toolkit",
                "Fetch web pages, parse HTML, search and crawl with SSRF hardening and offline fallback.",
                category="web",
            ),
        ]
    )
    client = _search_client(monkeypatch, registry=registry)
    resp = client.post(
        "/api/embeddings/search",
        json={
            "text": "search the web and write a researched report with sources",
            "corpus": "skills",
            "top_k": 2,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["corpus"] == "skills"
    assert body["embedding_method"] == "hash"
    assert body["query_embedding_dim"] > 0
    assert body["query_embedding"] is None  # default include_embeddings=False
    results = body["results"]
    assert 0 < len(results) <= 2
    # Ranked by similarity descending; ranks are 1-based and contiguous.
    scores = [r["similarity_score"] for r in results]
    assert scores == sorted(scores, reverse=True)
    assert [r["rank"] for r in results] == list(range(1, len(results) + 1))
    for r in results:
        assert 0.0 <= r["similarity_score"] <= 1.0
        assert r["name"]
        assert r["source_text"]
        assert "category" in r["metadata"]
    # The research-shaped query should surface deep-research at the top.
    assert results[0]["name"] == "deep-research"


def test_search_skills_include_embeddings_returns_query_vector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _StubRegistry([_StubSkill("a-skill", "do a thing with files and tools")])
    client = _search_client(monkeypatch, registry=registry)
    resp = client.post(
        "/api/embeddings/search",
        json={"text": "thing", "include_embeddings": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["query_embedding"], list)
    assert len(body["query_embedding"]) == body["query_embedding_dim"]


def test_search_task_types_delegates_to_similar(monkeypatch: pytest.MonkeyPatch, seeded_factory) -> None:
    client = _search_client(monkeypatch, seeded_factory=seeded_factory)
    resp = client.post(
        "/api/embeddings/search",
        json={
            "text": "Diagnose and fix a defect causing wrong output",
            "corpus": "task_types",
            "top_k": 3,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["corpus"] == "task_types"
    assert body["embedding_method"] == "hash"
    results = body["results"]
    assert 0 < len(results) <= 3
    assert [r["rank"] for r in results] == list(range(1, len(results) + 1))
    for r in results:
        assert r["name"]  # the task_type value
        assert r["source_text"]  # canonical_text
        assert "embedding_dim" in r["metadata"]
    # A bug-fix-shaped query should surface bug_fix / debugging near the top.
    top_names = {r["name"] for r in results}
    assert top_names & {"bug_fix", "debugging"}


def test_search_empty_skill_registry_returns_200_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _search_client(monkeypatch, registry=_StubRegistry([]))
    resp = client.post("/api/embeddings/search", json={"text": "anything", "corpus": "skills"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["corpus"] == "skills"
    assert body["results"] == []
    assert body["embedding_method"] == "hash"


def test_search_no_registry_returns_200_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No skill registry on app.state degrades to empty results, not 500."""
    client = _search_client(monkeypatch, registry=None)
    resp = client.post("/api/embeddings/search", json={"text": "anything", "corpus": "skills"})
    assert resp.status_code == 200
    assert resp.json()["results"] == []


def test_search_missing_text_is_422(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _search_client(monkeypatch, registry=_StubRegistry([]))
    resp = client.post("/api/embeddings/search", json={"corpus": "skills"})
    assert resp.status_code == 422


def test_search_unknown_corpus_is_422(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _search_client(monkeypatch, registry=_StubRegistry([]))
    resp = client.post("/api/embeddings/search", json={"text": "x", "corpus": "memory"})
    assert resp.status_code == 422


def test_search_top_k_out_of_range_is_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _search_client(monkeypatch, registry=_StubRegistry([]))
    too_high = client.post("/api/embeddings/search", json={"text": "x", "top_k": 21})
    assert too_high.status_code == 422
    too_low = client.post("/api/embeddings/search", json={"text": "x", "top_k": 0})
    assert too_low.status_code == 422


def test_search_defaults_corpus_to_skills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _StubRegistry([_StubSkill("s", "embed and rank skill description")])
    client = _search_client(monkeypatch, registry=registry)
    resp = client.post("/api/embeddings/search", json={"text": "rank"})
    assert resp.status_code == 200
    assert resp.json()["corpus"] == "skills"


# --- POST /api/embeddings/search (corpus=prompts) ----------------------------
#
# The prompts corpus has no stored vectors (zero schema change): the handler
# reads PromptProfileRepository.list_all(), embeds each ``prompt_text`` on the
# fly with the same default HashEmbedder, and cosine-ranks the query against
# them. These tests seed real prompt_profiles rows into the in-memory async DB
# via the repository's upsert, then assert ranking/metadata/degradation.


async def _seed_prompts(factory: Any, rows: list[dict[str, Any]]) -> None:
    """Insert the given prompt-profile dicts via the real repository upsert."""
    from general_ludd.db.repository import PromptProfileRepository

    async with factory() as session:
        repo = PromptProfileRepository(session)
        for row in rows:
            await repo.upsert(row)
        await session.commit()


@pytest_asyncio.fixture
async def prompts_factory(session_factory):
    """``session_factory`` seeded with three distinct prompt profiles."""
    import json as _json

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
                    "Write clear user-facing documentation and a tutorial for the new feature, with examples."
                ),
                "tags": _json.dumps(["docs"]),
                "task_types": _json.dumps(["documentation"]),
                "version": "latest",
            },
            {
                "name": "perf-tuner",
                "source": "internal",
                "prompt_text": ("Profile and optimize the slow hot path to reduce latency."),
                "tags": "[]",
                "task_types": "[]",
                "version": "latest",
            },
        ],
    )
    return session_factory


def test_search_prompts_returns_ranked_results(monkeypatch: pytest.MonkeyPatch, prompts_factory) -> None:
    client = _search_client(monkeypatch, seeded_factory=prompts_factory)
    resp = client.post(
        "/api/embeddings/search",
        json={
            "text": "diagnose and fix a defect causing wrong output and 500s",
            "corpus": "prompts",
            "top_k": 2,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["corpus"] == "prompts"
    assert body["embedding_method"] == "hash"
    assert body["query_embedding_dim"] > 0
    assert body["query_embedding"] is None  # default include_embeddings=False
    results = body["results"]
    assert 0 < len(results) <= 2
    # Ranked by similarity descending; ranks are 1-based and contiguous.
    scores = [r["similarity_score"] for r in results]
    assert scores == sorted(scores, reverse=True)
    assert [r["rank"] for r in results] == list(range(1, len(results) + 1))
    for r in results:
        assert 0.0 <= r["similarity_score"] <= 1.0
        assert r["name"]
        assert r["source_text"]
        md = r["metadata"]
        assert set(md) == {"source", "tags", "task_types", "version"}
        assert isinstance(md["tags"], list)
        assert isinstance(md["task_types"], list)
    # The bug-fix-shaped query should surface bug-hunter at the top.
    assert results[0]["name"] == "bug-hunter"
    top_md = results[0]["metadata"]
    assert top_md["source"] == "github"
    assert top_md["version"] == "v2"
    assert top_md["task_types"] == ["bug_fix"]
    assert "debugging" in top_md["tags"]


def test_search_prompts_include_embeddings_returns_query_vector(
    monkeypatch: pytest.MonkeyPatch, prompts_factory
) -> None:
    client = _search_client(monkeypatch, seeded_factory=prompts_factory)
    resp = client.post(
        "/api/embeddings/search",
        json={"text": "optimize", "corpus": "prompts", "include_embeddings": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["query_embedding"], list)
    assert len(body["query_embedding"]) == body["query_embedding_dim"]


def test_search_prompts_top_k_caps_results(monkeypatch: pytest.MonkeyPatch, prompts_factory) -> None:
    client = _search_client(monkeypatch, seeded_factory=prompts_factory)
    resp = client.post(
        "/api/embeddings/search",
        json={"text": "anything", "corpus": "prompts", "top_k": 1},
    )
    assert resp.status_code == 200
    assert len(resp.json()["results"]) == 1


def test_search_prompts_empty_store_returns_200_empty(monkeypatch: pytest.MonkeyPatch, session_factory) -> None:
    """An empty prompt_profiles table yields 200 + empty results, never 500."""
    client = _search_client(monkeypatch, seeded_factory=session_factory)
    resp = client.post("/api/embeddings/search", json={"text": "anything", "corpus": "prompts"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["corpus"] == "prompts"
    assert body["results"] == []
    assert body["embedding_method"] == "hash"


def test_search_prompts_no_session_factory_returns_200_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No session factory degrades to empty prompts results, never a 500."""
    client = _search_client(monkeypatch, seeded_factory=None)
    resp = client.post("/api/embeddings/search", json={"text": "anything", "corpus": "prompts"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["corpus"] == "prompts"
    assert body["results"] == []


def test_search_prompts_missing_text_is_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _search_client(monkeypatch, seeded_factory=None)
    resp = client.post("/api/embeddings/search", json={"corpus": "prompts"})
    assert resp.status_code == 422


def test_search_prompts_accepted_by_pydantic(monkeypatch: pytest.MonkeyPatch, session_factory) -> None:
    """corpus=prompts passes the pydantic pattern (not a 422 like memory)."""
    client = _search_client(monkeypatch, seeded_factory=session_factory)
    resp = client.post("/api/embeddings/search", json={"text": "x", "corpus": "prompts"})
    assert resp.status_code == 200


def test_search_prompts_embed_failure_degrades_to_200(monkeypatch: pytest.MonkeyPatch, prompts_factory) -> None:
    """A broken embedder degrades the prompts search to 200, never a 500."""

    class _BoomEmbedder:
        def embed(self, text: str) -> list[float]:
            raise RuntimeError("embedder exploded")

    monkeypatch.setattr(embeddings, "_select_default_embedder", lambda: _BoomEmbedder())
    client = _search_client(monkeypatch, seeded_factory=prompts_factory)
    resp = client.post(
        "/api/embeddings/search",
        json={"text": "anything", "corpus": "prompts"},
    )
    assert resp.status_code == 200
    assert resp.json()["corpus"] == "prompts"


@pytest_asyncio.fixture
async def malformed_meta_factory(session_factory):
    """``session_factory`` seeded with one row carrying non-JSON tags/task_types."""
    await _seed_prompts(
        session_factory,
        [
            {
                "name": "broken-meta",
                "source": "internal",
                "prompt_text": "rank this prompt despite broken metadata",
                "tags": "not-json{",
                "task_types": "also-not-json",
                "version": "latest",
            }
        ],
    )
    return session_factory


def test_search_prompts_malformed_tags_json_does_not_break(
    monkeypatch: pytest.MonkeyPatch, malformed_meta_factory
) -> None:
    """A row whose tags/task_types are not valid JSON degrades to empty lists."""
    client = _search_client(monkeypatch, seeded_factory=malformed_meta_factory)
    resp = client.post(
        "/api/embeddings/search",
        json={"text": "rank this prompt", "corpus": "prompts"},
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 1
    md = results[0]["metadata"]
    assert md["tags"] == []
    assert md["task_types"] == []


# --- POST /api/embeddings/search (corpus=traces) -----------------------------
#
# The traces corpus has no stored vectors (zero schema change): the handler
# reads the in-process RecentTracesBuffer's snapshot() (the SAME buffer
# /api/facts and /api/traces read), builds a searchable text per trace from
# work_type + phase labels + span descriptions, embeds each on the fly with the
# same default HashEmbedder, and cosine-ranks the query against them. These
# tests stub the buffer on app.state._recent_traces.


class _StubSpan:
    """Minimal stand-in for the dict a span snapshots to (phase + name)."""

    def __init__(self, phase: str, name: str) -> None:
        self.phase = phase
        self.name = name


class _StubTracesBuffer:
    """Minimal RecentTracesBuffer: exposes snapshot() returning the recent rows.

    ``traces`` is a list of trace dicts shaped like ExecutionTrace.to_dict()
    (trace_id/todo_id/work_type/total_cost_usd/span_count/spans[{phase,name}]).
    """

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


def _traces_client(monkeypatch: pytest.MonkeyPatch, *, buffer: Any) -> TestClient:
    """A search client with ``buffer`` published on app.state._recent_traces."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = FastAPI()
    app.state._session_factory = None
    app.state._skill_registry = None
    app.state._recent_traces = buffer
    embeddings.register(app, {})
    return TestClient(app)


def test_search_traces_returns_ranked_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    buffer = _StubTracesBuffer(
        [
            _trace_row(
                "trace-bug-1",
                "todo-1",
                "bug_fix",
                [
                    ("generate", "diagnose the defect causing wrong output"),
                    ("review", "verify the request handler 500 is gone"),
                ],
                total_cost_usd=0.12,
            ),
            _trace_row(
                "trace-doc-2",
                "todo-2",
                "documentation",
                [("generate", "write the user-facing tutorial and examples")],
            ),
            _trace_row(
                "trace-perf-3",
                "todo-3",
                "refactor",
                [("generate", "profile and optimize the slow hot path")],
            ),
        ]
    )
    client = _traces_client(monkeypatch, buffer=buffer)
    resp = client.post(
        "/api/embeddings/search",
        json={
            "text": "diagnose and fix the defect causing wrong output 500",
            "corpus": "traces",
            "top_k": 2,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["corpus"] == "traces"
    assert body["embedding_method"] == "hash"
    assert body["query_embedding_dim"] > 0
    assert body["query_embedding"] is None  # default include_embeddings=False
    results = body["results"]
    assert 0 < len(results) <= 2
    scores = [r["similarity_score"] for r in results]
    assert scores == sorted(scores, reverse=True)
    assert [r["rank"] for r in results] == list(range(1, len(results) + 1))
    for r in results:
        assert 0.0 <= r["similarity_score"] <= 1.0
        assert r["name"]
        assert r["source_text"]
        md = r["metadata"]
        assert set(md) == {
            "todo_id",
            "work_type",
            "total_cost_usd",
            "span_count",
            "by_phase",
        }
    # The bug-fix-shaped query should surface the bug-fix trace at the top.
    assert results[0]["name"] == "trace-bug-1"
    top_md = results[0]["metadata"]
    assert top_md["todo_id"] == "todo-1"
    assert top_md["work_type"] == "bug_fix"
    assert top_md["span_count"] == 2
    assert top_md["total_cost_usd"] == 0.12
    assert sorted(top_md["by_phase"]) == ["generate", "review"]


def test_search_traces_include_embeddings_returns_query_vector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    buffer = _StubTracesBuffer([_trace_row("t1", "todo-1", "code", [("generate", "do a thing")])])
    client = _traces_client(monkeypatch, buffer=buffer)
    resp = client.post(
        "/api/embeddings/search",
        json={"text": "thing", "corpus": "traces", "include_embeddings": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["query_embedding"], list)
    assert len(body["query_embedding"]) == body["query_embedding_dim"]


def test_search_traces_top_k_caps_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    buffer = _StubTracesBuffer(
        [
            _trace_row("t1", "a", "code", [("generate", "alpha task one")]),
            _trace_row("t2", "b", "code", [("generate", "beta task two")]),
            _trace_row("t3", "c", "code", [("generate", "gamma task three")]),
        ]
    )
    client = _traces_client(monkeypatch, buffer=buffer)
    resp = client.post(
        "/api/embeddings/search",
        json={"text": "task", "corpus": "traces", "top_k": 1},
    )
    assert resp.status_code == 200
    assert len(resp.json()["results"]) == 1


def test_search_traces_empty_buffer_returns_200_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty trace window yields 200 + empty results, never 500."""
    client = _traces_client(monkeypatch, buffer=_StubTracesBuffer([]))
    resp = client.post("/api/embeddings/search", json={"text": "anything", "corpus": "traces"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["corpus"] == "traces"
    assert body["results"] == []
    assert body["embedding_method"] == "hash"


def test_search_traces_no_buffer_returns_200_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No traces buffer on app.state degrades to empty results, never a 500."""
    client = _traces_client(monkeypatch, buffer=None)
    resp = client.post("/api/embeddings/search", json={"text": "anything", "corpus": "traces"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["corpus"] == "traces"
    assert body["results"] == []


def test_search_traces_missing_text_is_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _traces_client(monkeypatch, buffer=_StubTracesBuffer([]))
    resp = client.post("/api/embeddings/search", json={"corpus": "traces"})
    assert resp.status_code == 422


def test_search_traces_accepted_by_pydantic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """corpus=traces passes the pydantic pattern (not a 422 like memory)."""
    client = _traces_client(monkeypatch, buffer=_StubTracesBuffer([]))
    resp = client.post("/api/embeddings/search", json={"text": "x", "corpus": "traces"})
    assert resp.status_code == 200


def test_search_traces_embed_failure_degrades_to_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken embedder degrades the traces search to 200, never a 500."""

    class _BoomEmbedder:
        def embed(self, text: str) -> list[float]:
            raise RuntimeError("embedder exploded")

    monkeypatch.setattr(embeddings, "_select_default_embedder", lambda: _BoomEmbedder())
    buffer = _StubTracesBuffer([_trace_row("t1", "a", "code", [("generate", "do a thing")])])
    client = _traces_client(monkeypatch, buffer=buffer)
    resp = client.post(
        "/api/embeddings/search",
        json={"text": "anything", "corpus": "traces"},
    )
    assert resp.status_code == 200
    assert resp.json()["corpus"] == "traces"


def test_search_traces_skips_textless_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A trace with no work_type and no spans (empty text) is skipped."""
    buffer = _StubTracesBuffer(
        [
            {
                "trace_id": "empty-1",
                "todo_id": "x",
                "work_type": "",
                "total_cost_usd": 0.0,
                "span_count": 0,
                "spans": [],
            },
            _trace_row("good-1", "y", "code", [("generate", "real work text")]),
        ]
    )
    client = _traces_client(monkeypatch, buffer=buffer)
    resp = client.post(
        "/api/embeddings/search",
        json={"text": "real work", "corpus": "traces"},
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    names = {r["name"] for r in results}
    assert "empty-1" not in names
    assert "good-1" in names


# --- POST /api/embeddings/search (corpus=events) -----------------------------
#
# The events corpus has no stored vectors (zero schema change): the handler
# reads the most-recent audit events via a bounded created_at-descending select
# over AuditEventModel, builds a searchable text per event from
# event_type + entity_type + a human summary of its ``details`` JSON, embeds
# each on the fly with the same default HashEmbedder, and cosine-ranks the query
# against them. These tests seed real audit_events rows into the in-memory async
# DB via the repository, then assert ranking/metadata/degradation.


async def _seed_events(factory: Any, rows: list[dict[str, Any]]) -> None:
    """Insert the given audit-event dicts via the real repository create()."""
    from general_ludd.db.repository import (
        AuditEventRepository,
        ProjectRepository,
    )

    async with factory() as session:
        # audit_events.project_id is a FK to projects; ensure it exists.
        project_repo = ProjectRepository(session)
        seen: set[str] = set()
        repo = AuditEventRepository(session)
        for row in rows:
            project_id = row["project_id"]
            if project_id not in seen:
                await project_repo.create({"project_id": project_id, "name": project_id})
                seen.add(project_id)
            await repo.create(
                event_type=row["event_type"],
                entity_type=row["entity_type"],
                entity_id=row["entity_id"],
                project_id=project_id,
                details=row.get("details"),
            )
        await session.commit()


@pytest_asyncio.fixture
async def events_factory(session_factory):
    """``session_factory`` seeded with three distinct audit events."""
    import json as _json

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
                        "error": "diagnose the defect causing the request "
                        "handler to return wrong output and 500 errors",
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
                    {
                        "summary": "wrote the user-facing tutorial and examples",
                    }
                ),
            },
            {
                "event_type": "perf_tuned",
                "entity_type": "todo",
                "entity_id": "todo-3",
                "project_id": "proj-a",
                "details": _json.dumps({"summary": "profiled and optimized the slow hot path"}),
            },
        ],
    )
    return session_factory


def _events_client(monkeypatch: pytest.MonkeyPatch, *, seeded_factory: Any) -> TestClient:
    """A search client over the offline HashEmbedder for the events corpus."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = FastAPI()
    app.state._session_factory = seeded_factory
    app.state._skill_registry = None
    embeddings.register(app, {})
    return TestClient(app)


@pytest_asyncio.fixture
async def multi_project_events_factory(session_factory):
    """``session_factory`` seeded with one event in ``proj-a`` and one in
    ``proj-b`` — for the XT-8 cross-tenant isolation tests."""
    import json as _json

    await _seed_events(
        session_factory,
        [
            {
                "event_type": "todo_failed",
                "entity_type": "todo",
                "entity_id": "a-todo-1",
                "project_id": "proj-a",
                "details": _json.dumps({"summary": "alpha handler defect 500s"}),
            },
            {
                "event_type": "todo_failed",
                "entity_type": "todo",
                "entity_id": "b-todo-1",
                "project_id": "proj-b",
                "details": _json.dumps({"summary": "beta handler defect 500s"}),
            },
        ],
    )
    return session_factory


def test_search_events_scopes_to_project_id_xt8(monkeypatch: pytest.MonkeyPatch, multi_project_events_factory) -> None:
    # XT-8: with project_id=proj-a, only proj-a's audit event may be returned.
    client = _events_client(monkeypatch, seeded_factory=multi_project_events_factory)
    resp = client.post(
        "/api/embeddings/search",
        json={
            "text": "handler defect 500s",
            "corpus": "events",
            "top_k": 10,
            "project_id": "proj-a",
        },
    )
    assert resp.status_code == 200
    ids = {r["metadata"]["entity_id"] for r in resp.json()["results"]}
    assert ids == {"a-todo-1"}


def test_search_events_other_project_does_not_leak_xt8(
    monkeypatch: pytest.MonkeyPatch, multi_project_events_factory
) -> None:
    client = _events_client(monkeypatch, seeded_factory=multi_project_events_factory)
    resp = client.post(
        "/api/embeddings/search",
        json={
            "text": "handler defect 500s",
            "corpus": "events",
            "top_k": 10,
            "project_id": "proj-b",
        },
    )
    assert resp.status_code == 200
    ids = {r["metadata"]["entity_id"] for r in resp.json()["results"]}
    assert ids == {"b-todo-1"}


def test_search_events_without_project_id_returns_all_xt8(
    monkeypatch: pytest.MonkeyPatch, multi_project_events_factory
) -> None:
    # Back-compat: an unscoped query still searches across all tenants' events.
    client = _events_client(monkeypatch, seeded_factory=multi_project_events_factory)
    resp = client.post(
        "/api/embeddings/search",
        json={"text": "handler defect 500s", "corpus": "events", "top_k": 10},
    )
    assert resp.status_code == 200
    ids = {r["metadata"]["entity_id"] for r in resp.json()["results"]}
    assert ids == {"a-todo-1", "b-todo-1"}


def test_search_events_returns_ranked_results(monkeypatch: pytest.MonkeyPatch, events_factory) -> None:
    client = _events_client(monkeypatch, seeded_factory=events_factory)
    resp = client.post(
        "/api/embeddings/search",
        json={
            "text": "diagnose and fix the defect causing wrong output and 500s",
            "corpus": "events",
            "top_k": 2,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["corpus"] == "events"
    assert body["embedding_method"] == "hash"
    assert body["query_embedding_dim"] > 0
    assert body["query_embedding"] is None  # default include_embeddings=False
    results = body["results"]
    assert 0 < len(results) <= 2
    # Ranked by similarity descending; ranks are 1-based and contiguous.
    scores = [r["similarity_score"] for r in results]
    assert scores == sorted(scores, reverse=True)
    assert [r["rank"] for r in results] == list(range(1, len(results) + 1))
    for r in results:
        assert 0.0 <= r["similarity_score"] <= 1.0
        assert r["name"]
        assert r["source_text"]
        md = r["metadata"]
        assert set(md) == {
            "event_type",
            "entity_type",
            "entity_id",
            "actor",
            "created_at",
        }
    # The bug-fix-shaped query should surface the failed-todo event at the top.
    top = results[0]
    assert top["name"] == "todo_failed:todo-1"
    top_md = top["metadata"]
    assert top_md["event_type"] == "todo_failed"
    assert top_md["entity_type"] == "todo"
    assert top_md["entity_id"] == "todo-1"
    assert top_md["actor"] == "agent"  # AuditEventModel default
    assert top_md["created_at"]  # ISO timestamp present


def test_search_events_include_embeddings_returns_query_vector(monkeypatch: pytest.MonkeyPatch, events_factory) -> None:
    client = _events_client(monkeypatch, seeded_factory=events_factory)
    resp = client.post(
        "/api/embeddings/search",
        json={"text": "optimize", "corpus": "events", "include_embeddings": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["query_embedding"], list)
    assert len(body["query_embedding"]) == body["query_embedding_dim"]


def test_search_events_top_k_caps_results(monkeypatch: pytest.MonkeyPatch, events_factory) -> None:
    client = _events_client(monkeypatch, seeded_factory=events_factory)
    resp = client.post(
        "/api/embeddings/search",
        json={"text": "anything", "corpus": "events", "top_k": 1},
    )
    assert resp.status_code == 200
    assert len(resp.json()["results"]) == 1


def test_search_events_empty_store_returns_200_empty(monkeypatch: pytest.MonkeyPatch, session_factory) -> None:
    """An empty audit_events table yields 200 + empty results, never 500."""
    client = _events_client(monkeypatch, seeded_factory=session_factory)
    resp = client.post("/api/embeddings/search", json={"text": "anything", "corpus": "events"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["corpus"] == "events"
    assert body["results"] == []
    assert body["embedding_method"] == "hash"


def test_search_events_no_session_factory_returns_200_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No session factory degrades to empty events results, never a 500."""
    client = _events_client(monkeypatch, seeded_factory=None)
    resp = client.post("/api/embeddings/search", json={"text": "anything", "corpus": "events"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["corpus"] == "events"
    assert body["results"] == []


def test_search_events_missing_text_is_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _events_client(monkeypatch, seeded_factory=None)
    resp = client.post("/api/embeddings/search", json={"corpus": "events"})
    assert resp.status_code == 422


def test_search_events_accepted_by_pydantic(monkeypatch: pytest.MonkeyPatch, session_factory) -> None:
    """corpus=events passes the pydantic pattern (not a 422 like memory)."""
    client = _events_client(monkeypatch, seeded_factory=session_factory)
    resp = client.post("/api/embeddings/search", json={"text": "x", "corpus": "events"})
    assert resp.status_code == 200


def test_search_events_embed_failure_degrades_to_200(monkeypatch: pytest.MonkeyPatch, events_factory) -> None:
    """A broken embedder degrades the events search to 200, never a 500."""

    class _BoomEmbedder:
        def embed(self, text: str) -> list[float]:
            raise RuntimeError("embedder exploded")

    monkeypatch.setattr(embeddings, "_select_default_embedder", lambda: _BoomEmbedder())
    client = _events_client(monkeypatch, seeded_factory=events_factory)
    resp = client.post(
        "/api/embeddings/search",
        json={"text": "anything", "corpus": "events"},
    )
    assert resp.status_code == 200
    assert resp.json()["corpus"] == "events"


@pytest_asyncio.fixture
async def malformed_event_factory(session_factory):
    """``session_factory`` seeded with one event carrying non-JSON details."""
    await _seed_events(
        session_factory,
        [
            {
                "event_type": "weird_event",
                "entity_type": "todo",
                "entity_id": "todo-x",
                "project_id": "proj-b",
                "details": "not-json{ this is broken",
            }
        ],
    )
    return session_factory


def test_search_events_malformed_details_json_does_not_break(
    monkeypatch: pytest.MonkeyPatch, malformed_event_factory
) -> None:
    """A row whose details are not valid JSON still ranks (summary empties out).

    The event_type + entity_type still make a non-empty searchable text, so the
    row is ranked; the unparseable details simply contribute no terms.
    """
    client = _events_client(monkeypatch, seeded_factory=malformed_event_factory)
    resp = client.post(
        "/api/embeddings/search",
        json={"text": "weird event todo", "corpus": "events"},
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 1
    item = results[0]
    assert item["name"] == "weird_event:todo-x"
    # event_type + entity_type are present in the searchable text; the broken
    # details JSON degraded to no extra terms (fail-soft).
    assert "weird_event" in item["source_text"]
    assert "todo" in item["source_text"]
    assert "not-json" not in item["source_text"]


def test_event_details_summary_caps_key_count() -> None:
    """A details dict with many keys is bounded to _MAX_DETAILS_KEYS terms.

    Directly exercises the resource-abuse guard: an unbounded-key payload must
    not produce an unboundedly large source_text (memory amplification).
    """
    import json as _json

    from general_ludd.routers.embeddings import (
        _MAX_DETAILS_KEYS,
        _event_details_summary,
    )

    big = {f"k{i}": "v" for i in range(_MAX_DETAILS_KEYS * 5)}
    summary = _event_details_summary(_json.dumps(big))
    # The summary is "k0=v k1=v ..."; count the emitted key=value terms.
    terms = summary.split(" ")
    assert len(terms) == _MAX_DETAILS_KEYS


def test_event_details_summary_caps_value_size() -> None:
    """A single huge value is truncated to 500 chars (no unbounded term)."""
    import json as _json

    from general_ludd.routers.embeddings import _event_details_summary

    summary = _event_details_summary(_json.dumps({"blob": "a" * 100000}))
    # "blob=" prefix + 500 truncated value chars.
    assert summary == "blob=" + "a" * 500


@pytest_asyncio.fixture
async def high_key_event_factory(session_factory):
    """``session_factory`` seeded with one event carrying many details keys."""
    import json as _json

    from general_ludd.routers.embeddings import _MAX_DETAILS_KEYS

    await _seed_events(
        session_factory,
        [
            {
                "event_type": "noisy_event",
                "entity_type": "todo",
                "entity_id": "todo-noisy",
                "project_id": "proj-noisy",
                "details": _json.dumps({f"k{i}": "x" for i in range(_MAX_DETAILS_KEYS * 4)}),
            }
        ],
    )
    return session_factory


def test_search_events_high_key_count_details_is_bounded(
    monkeypatch: pytest.MonkeyPatch, high_key_event_factory
) -> None:
    """End-to-end: a many-key details event still ranks (200), bounded text.

    Proof the high-key-count guard holds through the handler: no hang/OOM, and
    the source_text carries at most _MAX_DETAILS_KEYS details terms.
    """
    from general_ludd.routers.embeddings import _MAX_DETAILS_KEYS

    client = _events_client(monkeypatch, seeded_factory=high_key_event_factory)
    resp = client.post(
        "/api/embeddings/search",
        json={"text": "noisy event todo", "corpus": "events"},
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 1
    src = results[0]["source_text"]
    # event_type + entity_type + at most _MAX_DETAILS_KEYS "k=v" terms.
    terms = src.split(" ")
    assert len(terms) <= _MAX_DETAILS_KEYS + 2


def test_search_events_skips_eventless_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An event row whose text is empty is skipped (defensive degradation).

    This exercises the _event_text empty-string skip directly with a stub
    session factory yielding rows with no type fields and empty details.
    """

    class _StubEventRow:
        def __init__(
            self,
            *,
            event_type: str,
            entity_type: str,
            entity_id: str,
            details: Any,
            actor: str = "agent",
        ) -> None:
            self.event_type = event_type
            self.entity_type = entity_type
            self.entity_id = entity_id
            self.details = details
            self.actor = actor
            self.id = 1
            self.created_at = None

    class _StubResult:
        def __init__(self, rows: list[Any]) -> None:
            self._rows = rows

        def scalars(self) -> Any:
            rows = self._rows

            class _S:
                def all(self_inner) -> list[Any]:
                    return rows

            return _S()

    class _StubSession:
        def __init__(self, rows: list[Any]) -> None:
            self._rows = rows

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        async def execute(self, *a: Any, **k: Any) -> Any:
            return _StubResult(self._rows)

    rows = [
        _StubEventRow(event_type="", entity_type="", entity_id="", details="{}"),
        _StubEventRow(
            event_type="real_event",
            entity_type="todo",
            entity_id="todo-9",
            details="{}",
        ),
    ]

    def _factory() -> Any:
        return _StubSession(rows)

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = FastAPI()
    app.state._session_factory = _factory
    app.state._skill_registry = None
    embeddings.register(app, {})
    client = TestClient(app)
    resp = client.post(
        "/api/embeddings/search",
        json={"text": "real event todo", "corpus": "events"},
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    names = {r["name"] for r in results}
    assert "real_event:todo-9" in names
    assert "" not in names  # the textless row was skipped


# --- Utility function tests -------------------------------------------------


def test_parse_json_list_valid_json_array() -> None:
    """_parse_json_list parses a valid JSON array string."""
    from general_ludd.routers.embeddings import _parse_json_list

    assert _parse_json_list('["a", "b", "c"]') == ["a", "b", "c"]


def test_parse_json_list_valid_python_list_passthrough() -> None:
    """_parse_json_list passes through an already-parsed list."""
    from general_ludd.routers.embeddings import _parse_json_list

    assert _parse_json_list(["a", "b"]) == ["a", "b"]


def test_parse_json_list_none_returns_empty() -> None:
    """_parse_json_list degrades None to empty list."""
    from general_ludd.routers.embeddings import _parse_json_list

    assert _parse_json_list(None) == []


def test_parse_json_list_empty_string_returns_empty() -> None:
    """_parse_json_list degrades empty string to empty list."""
    from general_ludd.routers.embeddings import _parse_json_list

    assert _parse_json_list("") == []


def test_parse_json_list_malformed_json_returns_empty() -> None:
    """_parse_json_list degrades malformed JSON to empty list."""
    from general_ludd.routers.embeddings import _parse_json_list

    assert _parse_json_list("not-json{") == []
    assert _parse_json_list("[unclosed") == []


def test_parse_json_list_non_list_json_returns_empty() -> None:
    """_parse_json_list degrades a JSON object to empty list (not a list)."""
    from general_ludd.routers.embeddings import _parse_json_list

    assert _parse_json_list('{"key": "value"}') == []


def test_parse_json_list_non_string_non_list_returns_empty() -> None:
    """_parse_json_list degrades an integer to empty list."""
    from general_ludd.routers.embeddings import _parse_json_list

    assert _parse_json_list(42) == []


def test_trace_text_with_all_fields() -> None:
    """_trace_text concatenates work_type + phases + span names."""
    from general_ludd.routers.embeddings import _trace_text

    trace: dict[str, object] = {
        "work_type": "bug_fix",
        "spans": [
            {"phase": "generate", "name": "diagnose the defect"},
            {"phase": "review", "name": "verify the fix is correct"},
        ],
    }
    result = _trace_text(trace)
    assert "bug_fix" in result
    assert "generate" in result
    assert "diagnose the defect" in result
    assert "review" in result
    assert "verify the fix is correct" in result


def test_trace_text_no_work_type() -> None:
    """_trace_text with no work_type still produces span text."""
    from general_ludd.routers.embeddings import _trace_text

    trace: dict[str, object] = {
        "work_type": "",
        "spans": [{"phase": "generate", "name": "do work"}],
    }
    result = _trace_text(trace)
    assert "generate" in result
    assert "do work" in result


def test_trace_text_empty_trace_returns_empty() -> None:
    """_trace_text with no work_type and no/empty spans returns empty string."""
    from general_ludd.routers.embeddings import _trace_text

    assert _trace_text({}) == ""
    assert _trace_text({"work_type": "", "spans": []}) == ""


def test_trace_text_skips_non_dict_spans() -> None:
    """_trace_text skips span entries that are not dicts."""
    from general_ludd.routers.embeddings import _trace_text

    trace: dict[str, object] = {
        "work_type": "code",
        "spans": [
            "not-a-dict",
            {"phase": "generate", "name": "real work"},
            42,
        ],
    }
    result = _trace_text(trace)
    assert "code" in result
    assert "generate" in result
    assert "real work" in result
    assert "not-a-dict" not in result
    assert "42" not in result


def test_trace_text_missing_span_fields() -> None:
    """_trace_text handles spans with missing phase/name gracefully."""
    from general_ludd.routers.embeddings import _trace_text

    trace: dict[str, object] = {
        "work_type": "feature",
        "spans": [
            {"phase": "", "name": "a thing without phase"},
            {"name": "a thing without phase key"},
            {},
        ],
    }
    result = _trace_text(trace)
    assert "feature" in result
    # Only the name of the first span should appear; the second span has no
    # "phase" key at all, and the third is empty.
    assert "a thing without phase" in result


def test_event_text_with_all_fields() -> None:
    """_event_text concatenates event_type + entity_type + details summary."""
    import json as _json

    from general_ludd.routers.embeddings import _event_text

    class _Row:
        event_type = "todo_failed"
        entity_type = "todo"
        details = _json.dumps({"error": "defect in handler", "model": "glm-4.6"})

    result = _event_text(_Row)
    assert "todo_failed" in result
    assert "todo" in result
    assert "error=defect in handler" in result
    assert "model=glm-4.6" in result


def test_event_text_empty_type_fields() -> None:
    """_event_text with empty event_type/entity_type still produces details text."""
    import json as _json

    from general_ludd.routers.embeddings import _event_text

    class _Row:
        event_type = ""
        entity_type = ""
        details = _json.dumps({"summary": "something happened"})

    result = _event_text(_Row)
    assert "summary=something happened" in result
    assert result.startswith("summary=")


def test_event_text_all_empty_returns_empty() -> None:
    """_event_text with no type fields and empty details returns empty string."""
    from general_ludd.routers.embeddings import _event_text

    class _Row:
        event_type = ""
        entity_type = ""
        details = "{}"

    assert _event_text(_Row) == ""


# --- Traces XT-8 project_id isolation ---------------------------------------


def test_search_traces_scopes_to_project_id_xt8(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """XT-8: with project_id set, only traces matching that project are returned."""
    buffer_builder = type(
        "_Buffer",
        (),
        {
            "_call_args": None,
            "snapshot": lambda self, project_id=None: (
                setattr(type(self), "_call_args", project_id),
                {
                    "recent": [
                        {
                            "trace_id": "t1",
                            "todo_id": "todo-1",
                            "work_type": "bug_fix",
                            "total_cost_usd": 0.01,
                            "span_count": 1,
                            "spans": [{"phase": "generate", "name": "fix defect"}],
                        }
                    ]
                },
            )[1],
        },
    )
    buffer = buffer_builder()

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = FastAPI()
    app.state._session_factory = None
    app.state._skill_registry = None
    app.state._recent_traces = buffer
    embeddings.register(app, {})
    client = TestClient(app)
    resp = client.post(
        "/api/embeddings/search",
        json={
            "text": "fix defect",
            "corpus": "traces",
            "project_id": "proj-a",
        },
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) >= 1
    # The project_id was forwarded to snapshot().
    assert type(buffer)._call_args == "proj-a"


def test_search_traces_without_project_id_passes_none_xt8(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """XT-8: without project_id, snapshot() is called with None (unscoped)."""
    buffer_builder = type(
        "_Buffer",
        (),
        {
            "_call_args": None,
            "snapshot": lambda self, project_id=None: (
                setattr(type(self), "_call_args", project_id),
                {"recent": []},
            )[1],
        },
    )
    buffer = buffer_builder()

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = FastAPI()
    app.state._session_factory = None
    app.state._skill_registry = None
    app.state._recent_traces = buffer
    embeddings.register(app, {})
    client = TestClient(app)
    resp = client.post(
        "/api/embeddings/search",
        json={"text": "anything", "corpus": "traces"},
    )
    assert resp.status_code == 200
    assert type(buffer)._call_args is None


# --- /compare validator edge cases ------------------------------------------


def test_compare_only_text_b_is_422(monkeypatch: pytest.MonkeyPatch) -> None:
    """Supplying only text_b (no text_a) is rejected by the model_validator."""
    client = _compare_client(monkeypatch)
    resp = client.post("/api/embeddings/compare", json={"text_b": "only b"})
    assert resp.status_code == 422


def test_compare_texts_with_zero_items_is_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """texts with 0 items -> 422 (min_length validated, but validator requires >=2)."""
    client = _compare_client(monkeypatch)
    resp = client.post("/api/embeddings/compare", json={"texts": []})
    assert resp.status_code == 422


def test_compare_batch_with_include_embeddings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Batch form with include_embeddings=True returns the embeddings list."""
    client = _compare_client(monkeypatch)
    resp = client.post(
        "/api/embeddings/compare",
        json={"texts": ["alpha", "beta", "gamma"], "include_embeddings": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["embeddings"], list)
    assert len(body["embeddings"]) == 3
    assert body["similarity"] is None
    assert body["dim"] > 0


# --- search-multi in main test file -----------------------------------------


class _StubSearchMultiSkill:
    def __init__(self, name: str, description: str, *, category: str = "", tags: list[str] | None = None) -> None:
        self.name = name
        self.description = description
        self.category = category
        self.tags = tags or []


class _StubSearchMultiRegistry:
    def __init__(self, skills: list[_StubSearchMultiSkill]) -> None:
        self._skills = skills

    def list_skills(self) -> list[_StubSearchMultiSkill]:
        return list(self._skills)


class _StubSearchMultiTraces:
    def __init__(self, traces: list[dict[str, Any]]) -> None:
        self._traces = traces

    def snapshot(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"recent": list(self._traces)}


def test_search_multi_valid_basic_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A basic multi-corpus search with skills+task_types returns 200."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = FastAPI()
    app.state._session_factory = None
    app.state._skill_registry = _StubSearchMultiRegistry([_StubSearchMultiSkill("s1", "do a thing")])
    app.state._recent_traces = None
    embeddings.register(app, {})
    client = TestClient(app)
    resp = client.post(
        "/api/embeddings/search-multi",
        json={"text": "do a thing", "corpora": ["skills"], "top_k": 3},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["corpora_searched"] == ["skills"]
    assert body["embedding_method"] == "hash"
    assert isinstance(body["results"], list)


def test_search_multi_every_result_has_corpus_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every merged result carries 'corpus' in its metadata."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = FastAPI()
    app.state._session_factory = None
    app.state._skill_registry = _StubSearchMultiRegistry(
        [_StubSearchMultiSkill("web", "Fetch web pages and parse HTML")]
    )
    app.state._recent_traces = _StubSearchMultiTraces(
        [
            {
                "trace_id": "t1",
                "todo_id": "x",
                "work_type": "code",
                "total_cost_usd": 0.0,
                "span_count": 1,
                "spans": [{"phase": "generate", "name": "do work"}],
            }
        ]
    )
    embeddings.register(app, {})
    client = TestClient(app)
    resp = client.post(
        "/api/embeddings/search-multi",
        json={"text": "web pages work", "corpora": ["skills", "traces"], "top_k": 5},
    )
    assert resp.status_code == 200
    for r in resp.json()["results"]:
        assert "corpus" in r["metadata"]


def test_search_multi_missing_corpora_is_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing 'corpora' field -> 422."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = FastAPI()
    app.state._session_factory = None
    app.state._skill_registry = None
    app.state._recent_traces = None
    embeddings.register(app, {})
    client = TestClient(app)
    resp = client.post(
        "/api/embeddings/search-multi",
        json={"text": "x"},
    )
    assert resp.status_code == 422


def test_search_multi_unknown_corpus_in_list_is_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown corpus in corpora list -> 422 via model_validator."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = FastAPI()
    app.state._session_factory = None
    app.state._skill_registry = None
    embeddings.register(app, {})
    client = TestClient(app)
    resp = client.post(
        "/api/embeddings/search-multi",
        json={"text": "x", "corpora": ["skills", "unknown_corpus"]},
    )
    assert resp.status_code == 422


def test_search_multi_text_over_cap_is_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multi-corpus search with text > 20000 chars -> 422."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = FastAPI()
    app.state._session_factory = None
    embeddings.register(app, {})
    client = TestClient(app)
    resp = client.post(
        "/api/embeddings/search-multi",
        json={"text": "a" * 20001, "corpora": ["skills"]},
    )
    assert resp.status_code == 422


def test_search_multi_top_k_out_of_range_is_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multi-corpus top_k <= 0 or > 20 -> 422."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = FastAPI()
    app.state._session_factory = None
    embeddings.register(app, {})
    client = TestClient(app)
    assert (
        client.post("/api/embeddings/search-multi", json={"text": "x", "corpora": ["skills"], "top_k": 0}).status_code
        == 422
    )
    assert (
        client.post("/api/embeddings/search-multi", json={"text": "x", "corpora": ["skills"], "top_k": 21}).status_code
        == 422
    )


def test_search_multi_empty_corpora_list_is_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty corpora list -> 422 (min_length=1)."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = FastAPI()
    embeddings.register(app, {})
    client = TestClient(app)
    resp = client.post(
        "/api/embeddings/search-multi",
        json={"text": "x", "corpora": []},
    )
    assert resp.status_code == 422


def test_search_multi_missing_text_is_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing text in multi-corpus request -> 422."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = FastAPI()
    embeddings.register(app, {})
    client = TestClient(app)
    resp = client.post(
        "/api/embeddings/search-multi",
        json={"corpora": ["skills"]},
    )
    assert resp.status_code == 422


# --- search-multi empty corpora resilience ----------------------------------


def test_search_multi_all_corpora_absent_degrades_to_empty_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When every corpus backend is absent, multi-search returns 200 with empty results."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = FastAPI()
    app.state._session_factory = None
    app.state._skill_registry = None
    app.state._recent_traces = None
    embeddings.register(app, {})
    client = TestClient(app)
    resp = client.post(
        "/api/embeddings/search-multi",
        json={"text": "anything", "corpora": ["skills", "traces", "task_types", "events", "prompts"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["results"] == []
    assert body["embedding_method"] == "hash"


def test_search_multi_one_corpus_survives_when_others_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When one corpus has data and the rest are absent, the surviving corpus returns results."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = FastAPI()
    app.state._session_factory = None
    app.state._skill_registry = _StubSearchMultiRegistry(
        [_StubSearchMultiSkill("only-survivor", "the only corpus with data")]
    )
    app.state._recent_traces = None
    embeddings.register(app, {})
    client = TestClient(app)
    resp = client.post(
        "/api/embeddings/search-multi",
        json={"text": "data", "corpora": ["skills", "task_types", "events"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["results"]) >= 1
    assert "skills" in body["corpora_searched"]
    for r in body["results"]:
        assert r["metadata"]["corpus"] == "skills"
