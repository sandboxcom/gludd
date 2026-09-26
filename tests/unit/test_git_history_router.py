"""Unit tests for git_history router."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from general_ludd.routers import git_history


@pytest.fixture
def app():
    app = FastAPI()
    git_history.register(app, {})
    return app


class TestGitHistory:
    @pytest.mark.asyncio
    async def test_history_stats(self, app):
        with patch("general_ludd.history.git_indexer.GitHistoryIndexer.stats") as mock_stats:
            mock_stats.return_value = {"commits": 10}
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/git/history/stats")
            assert response.status_code == 200
            assert response.json() == {"commits": 10}

    @pytest.mark.asyncio
    async def test_history_search(self, app):
        with patch("general_ludd.history.git_indexer.GitHistoryIndexer.search") as mock_search:
            commit = MagicMock()
            commit.to_dict.return_value = {"hash": "abc123"}
            mock_search.return_value = [commit]
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/git/history?q=test")
            assert response.status_code == 200
            assert response.json() == [{"hash": "abc123"}]

    @pytest.mark.asyncio
    async def test_reindex_history(self, app):
        with patch("general_ludd.history.git_indexer.GitHistoryIndexer.index") as mock_index:
            mock_index.return_value = 5
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/api/git/history/reindex")
            assert response.status_code == 200
            assert response.json() == {"status": "ok", "indexed": 5}
