"""Behavioral coverage for the git-history API router."""

from __future__ import annotations

from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.git_history import register


def _client(indexer: Mock) -> TestClient:
    app = FastAPI()
    with patch(
        "general_ludd.routers.git_history.GitHistoryIndexer",
        return_value=indexer,
    ):
        register(app, {})
    return TestClient(app)


def test_history_search_forwards_filters_and_serializes_results() -> None:
    result = Mock()
    result.to_dict.return_value = {"hash": "abc", "subject": "release"}
    indexer = Mock()
    indexer.search.return_value = [result]

    response = _client(indexer).get(
        "/api/git/history",
        params={
            "q": "beta3",
            "since": "2026-07-01",
            "author": "Ada",
            "path": "src/",
            "limit": 7,
            "offset": 2,
        },
    )

    assert response.status_code == 200
    assert response.json() == [{"hash": "abc", "subject": "release"}]
    indexer.search.assert_called_once_with(
        query="beta3",
        since="2026-07-01",
        author="Ada",
        path_filter="src/",
        limit=7,
        offset=2,
    )


def test_history_stats_and_reindex_success() -> None:
    indexer = Mock()
    indexer.stats.return_value = {"commits": 11}
    indexer.index.return_value = 9
    client = _client(indexer)

    assert client.get("/api/git/history/stats").json() == {"commits": 11}
    assert client.post("/api/git/history/reindex").json() == {
        "status": "ok",
        "indexed": 9,
    }


def test_history_endpoints_convert_backend_failures_to_http_500() -> None:
    indexer = Mock()
    indexer.search.side_effect = RuntimeError("search unavailable")
    indexer.stats.side_effect = RuntimeError("stats unavailable")
    indexer.index.side_effect = RuntimeError("index unavailable")
    client = _client(indexer)

    assert client.get("/api/git/history").json() == {
        "detail": "Search failed: search unavailable"
    }
    assert client.get("/api/git/history/stats").json() == {
        "detail": "Stats failed: stats unavailable"
    }
    assert client.post("/api/git/history/reindex").json() == {
        "detail": "Reindex failed: index unavailable"
    }
