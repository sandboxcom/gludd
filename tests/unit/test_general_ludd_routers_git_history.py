from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.git_history import register


@pytest.fixture
def mock_indexer() -> Iterator[MagicMock]:
    indexer = MagicMock()
    indexer.search = MagicMock()
    indexer.stats = MagicMock()
    indexer.index = MagicMock()
    indexer.search.return_value = []
    indexer.stats.return_value = {"total_commits": 0}
    indexer.index.return_value = 42

    with patch(
        "general_ludd.routers.git_history.GitHistoryIndexer",
        return_value=indexer,
    ):
        yield indexer


@pytest.fixture
def client(mock_indexer: MagicMock) -> TestClient:
    app = FastAPI()
    daemon_state: dict[str, object] = {}
    register(app, daemon_state)
    return TestClient(app)


class TestGitHistorySearch:
    def test_search_returns_empty_list(self, client: TestClient, mock_indexer: MagicMock) -> None:
        mock_indexer.search.return_value = []
        response = client.get("/api/git/history?q=test")
        assert response.status_code == 200
        assert response.json() == []

    def test_search_with_all_params(self, client: TestClient, mock_indexer: MagicMock) -> None:
        response = client.get(
            "/api/git/history",
            params={
                "q": "fix",
                "since": "2024-01-01",
                "author": "dev",
                "path": "src/",
                "limit": 50,
                "offset": 10,
            },
        )
        assert response.status_code == 200

    def test_search_default_params(self, client: TestClient, mock_indexer: MagicMock) -> None:
        response = client.get("/api/git/history")
        assert response.status_code == 200
        mock_indexer.search.assert_called_once()

    def test_search_limit_below_1_returns_422(self, client: TestClient) -> None:
        response = client.get("/api/git/history?limit=0")
        assert response.status_code == 422

    def test_search_limit_above_500_returns_422(self, client: TestClient) -> None:
        response = client.get("/api/git/history?limit=501")
        assert response.status_code == 422

    def test_search_negative_offset_returns_422(self, client: TestClient) -> None:
        response = client.get("/api/git/history?offset=-1")
        assert response.status_code == 422

    def test_search_handles_exception(self, client: TestClient, mock_indexer: MagicMock) -> None:
        mock_indexer.search.side_effect = RuntimeError("boom")
        response = client.get("/api/git/history")
        assert response.status_code == 500
        data = response.json()
        assert "detail" in data


class TestGitHistoryStats:
    def test_stats_returns_dict(self, client: TestClient, mock_indexer: MagicMock) -> None:
        mock_indexer.stats.return_value = {"total_commits": 150, "total_files": 20}
        response = client.get("/api/git/history/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["total_commits"] == 150

    def test_stats_handles_exception(self, client: TestClient, mock_indexer: MagicMock) -> None:
        mock_indexer.stats.side_effect = RuntimeError("stats error")
        response = client.get("/api/git/history/stats")
        assert response.status_code == 500


class TestGitHistoryReindex:
    def test_reindex_returns_ok(self, client: TestClient, mock_indexer: MagicMock) -> None:
        mock_indexer.index.return_value = 42
        response = client.post("/api/git/history/reindex")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["indexed"] == 42

    def test_reindex_handles_exception(self, client: TestClient, mock_indexer: MagicMock) -> None:
        mock_indexer.index.side_effect = RuntimeError("reindex error")
        response = client.post("/api/git/history/reindex")
        assert response.status_code == 500
