"""Unit tests for routers/research.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.research import register


@pytest.fixture
def client():
    app = FastAPI()
    app.state._searx_client = None
    register(app, {})
    return TestClient(app)


class TestResearchValidateEndpoint:
    def test_post_default_parameters(self, client):
        resp = client.post("/api/research/validate", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["query_count"] == 0
        assert data["findings_count"] == 0
        assert data["searx_available"] is False

    def test_post_returns_200_with_mocked_researcher(self, client):
        with patch("general_ludd.agents.researcher.ResearcherAgent") as mock_cls:
            mock_agent = MagicMock()
            mock_agent.research = AsyncMock()
            mock_report = MagicMock()
            mock_report.model_dump.return_value = {
                "report_id": "r1", "query": "test", "findings": [],
            }
            mock_report.findings = []
            mock_agent.research.return_value = mock_report
            mock_cls.return_value = mock_agent

            resp = client.post("/api/research/validate", json={
                "queries": ["test query"],
                "categories": ["general"],
                "time_range": "month",
                "max_results": 5,
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["query_count"] == 1
            assert data["findings_count"] == 0
            assert data["searx_available"] is False
            assert len(data["reports"]) == 1

    def test_post_handles_researcher_exception_gracefully(self, client):
        with patch("general_ludd.agents.researcher.ResearcherAgent") as mock_cls:
            mock_agent = MagicMock()
            mock_agent.research = AsyncMock(side_effect=RuntimeError("boom"))
            mock_cls.return_value = mock_agent

            resp = client.post("/api/research/validate", json={
                "queries": ["bad query"],
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["query_count"] == 1
            assert data["findings_count"] == 0
            assert "Research failed" in data["reports"][0]["summary"]
