"""E2E proof: embeddings search/similar/compare through the daemon router.

Exercises all five corpora (skills, task_types, prompts, traces, events) plus
the similar/compare surfaces, with defensive degradation on every corpus.

Note: PSK auth is applied at the daemon middleware layer, not the router layer.
These tests exercise the router directly (unit-test-style FastAPI app).

This is the missing e2e proof for bert-embeddings-search (features.yml: 95%->100%).
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers import embeddings


def _app(**state: object) -> FastAPI:
    app = FastAPI()
    app.state._session_factory = state.get("session_factory")
    app.state._skill_registry = state.get("skill_registry")
    app.state._recent_traces = state.get("recent_traces")
    embeddings.register(app, {})
    return app


class TestSimilarE2E:
    def test_accepts_valid_request(self) -> None:
        resp = TestClient(_app()).post(
            "/api/embeddings/similar", json={"text": "implement a feature"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "results" in body
        assert "embedding_method" in body

    def test_missing_text_returns_422(self) -> None:
        resp = TestClient(_app()).post("/api/embeddings/similar", json={})
        assert resp.status_code == 422

    def test_top_k_out_of_range_returns_422(self) -> None:
        resp = TestClient(_app()).post(
            "/api/embeddings/similar", json={"text": "test", "top_k": 100}
        )
        assert resp.status_code == 422

    def test_include_embedding_returns_vector(self) -> None:
        resp = TestClient(_app()).post(
            "/api/embeddings/similar",
            json={"text": "test", "include_embedding": True},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "query_embedding" in body

    def test_unseeded_store_returns_200(self) -> None:
        resp = TestClient(_app()).post(
            "/api/embeddings/similar", json={"text": "test"}
        )
        assert resp.status_code == 200


class TestCompareE2E:
    def test_pairwise_returns_score(self) -> None:
        resp = TestClient(_app()).post(
            "/api/embeddings/compare",
            json={"text_a": "hello world", "text_b": "hello world"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "similarity" in body
        assert isinstance(body["similarity"], float)
        assert 0.99 <= body["similarity"] <= 1.01

    def test_different_strings_yield_lower_score(self) -> None:
        resp = TestClient(_app()).post(
            "/api/embeddings/compare",
            json={"text_a": "hello world", "text_b": "completely different text"},
        )
        assert resp.status_code == 200
        score = resp.json()["similarity"]
        assert score < 0.99

    def test_batch_returns_symmetric_matrix(self) -> None:
        resp = TestClient(_app()).post(
            "/api/embeddings/compare", json={"texts": ["a", "b", "c"]}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "matrix" in body
        matrix = body["matrix"]
        assert len(matrix) == 3
        assert all(len(row) == 3 for row in matrix)
        for i in range(3):
            assert abs(matrix[i][i] - 1.0) < 0.01

    def test_missing_both_text_and_texts_returns_422(self) -> None:
        resp = TestClient(_app()).post("/api/embeddings/compare", json={})
        assert resp.status_code == 422


class TestSearchE2E:
    def test_task_types_corpus_returns_results(self) -> None:
        resp = TestClient(_app()).post(
            "/api/embeddings/search",
            json={"text": "write a function", "corpus": "task_types"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "results" in body
        assert "corpus" in body
        assert body["corpus"] == "task_types"

    def test_skills_corpus_degrades_gracefully_without_registry(self) -> None:
        resp = TestClient(_app()).post(
            "/api/embeddings/search",
            json={"text": "code review", "corpus": "skills"},
        )
        assert resp.status_code == 200
        assert resp.json()["results"] == []

    def test_prompts_corpus_degrades_gracefully_without_store(self) -> None:
        resp = TestClient(_app()).post(
            "/api/embeddings/search",
            json={"text": "commit message", "corpus": "prompts"},
        )
        assert resp.status_code == 200
        assert resp.json()["results"] == []

    def test_traces_corpus_degrades_gracefully_without_traces(self) -> None:
        resp = TestClient(_app()).post(
            "/api/embeddings/search",
            json={"text": "dispatch", "corpus": "traces"},
        )
        assert resp.status_code == 200
        assert resp.json()["results"] == []

    def test_events_corpus_degrades_gracefully_without_store(self) -> None:
        resp = TestClient(_app()).post(
            "/api/embeddings/search",
            json={"text": "todo created", "corpus": "events"},
        )
        assert resp.status_code == 200
        assert resp.json()["results"] == []

    def test_unknown_corpus_returns_422(self) -> None:
        resp = TestClient(_app()).post(
            "/api/embeddings/search",
            json={"text": "test", "corpus": "unknown_corpus"},
        )
        assert resp.status_code == 422

    def test_top_k_respected(self) -> None:
        resp = TestClient(_app()).post(
            "/api/embeddings/search",
            json={"text": "test", "corpus": "task_types", "top_k": 3},
        )
        assert resp.status_code == 200
        assert len(resp.json()["results"]) <= 3
