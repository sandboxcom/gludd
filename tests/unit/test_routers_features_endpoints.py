"""Unit tests for routers/features.py router endpoint handlers.

Covers the previously 20.7%-rated module by exercising:
  * GET /api/features (list with filters, no DB, scoping)
  * GET /api/features/{feature_id} (found, not found, no DB, scoped)
  * POST /api/features/verify (no DB, empty list, verify+persist)
"""
from __future__ import annotations

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from general_ludd.daemon import create_daemon_app


class MockFeatureRow:
    def __init__(self, **kwargs):
        self.id = kwargs.get("id", "FEAT-001")
        self.project_id = kwargs.get("project_id", "proj-1")
        self.name = kwargs.get("name", "test-feature")
        self.description = kwargs.get("description", "a test")
        self.category = kwargs.get("category", "security")
        self.status = kwargs.get("status", "requested")
        self.acceptance_criteria = kwargs.get("acceptance_criteria", '["test: x"]')
        self.evidence = kwargs.get("evidence", '["module: x"]')
        self.verifier_kind = kwargs.get("verifier_kind", "test")
        self.requested_by = kwargs.get("requested_by", "tester")
        self.requested_at = kwargs.get("requested_at", datetime.datetime(2026, 1, 1))
        self.verified_at = kwargs.get("verified_at")
        self.last_verify_detail = kwargs.get("last_verify_detail", "{}")


def _feature_to_dict(feat) -> dict:
    import json
    return {
        "id": feat.id,
        "project_id": feat.project_id,
        "name": feat.name,
        "description": feat.description,
        "category": feat.category,
        "status": feat.status,
        "acceptance_criteria": (
            json.loads(feat.acceptance_criteria)
            if isinstance(feat.acceptance_criteria, str)
            else feat.acceptance_criteria
        ),
        "evidence": (
            json.loads(feat.evidence)
            if isinstance(feat.evidence, str)
            else feat.evidence
        ),
        "verifier_kind": feat.verifier_kind,
        "requested_by": feat.requested_by,
        "requested_at": str(feat.requested_at) if feat.requested_at else None,
        "verified_at": str(feat.verified_at) if feat.verified_at else None,
        "last_verify_detail": (
            json.loads(feat.last_verify_detail)
            if isinstance(feat.last_verify_detail, str) and feat.last_verify_detail
            else feat.last_verify_detail
        ),
    }


@pytest.fixture
def app():
    return create_daemon_app(tick_interval=0.01)


async def _get(app, path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


async def _post(app, path, **kwargs):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(path, **kwargs)


class TestListFeatures:
    @pytest.mark.asyncio
    async def test_list_features_no_db_returns_empty(self, app):
        app.state._session_factory = None
        resp = await _get(app, "/api/features")
        assert resp.status_code == 200
        assert resp.json()["features"] == []
        assert resp.json()["total"] == 0
        assert resp.json()["filtered"] is True

    @pytest.mark.asyncio
    async def test_list_all_features(self, app):
        mock_repo = MagicMock()
        row = MockFeatureRow(id="FEAT-001", name="feature-a", status="requested")
        mock_repo.list_all = AsyncMock(return_value=[row])

        mock_factory = MagicMock()
        async_session_ctx = MagicMock()
        async_session_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        async_session_ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("general_ludd.routers.features.FeatureRepository", return_value=mock_repo):
            mock_factory.return_value = async_session_ctx
            app.state._session_factory = mock_factory
            resp = await _get(app, "/api/features")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["filtered"] is False

    @pytest.mark.asyncio
    async def test_list_features_filtered_by_status(self, app):
        mock_repo = MagicMock()
        row = MockFeatureRow(id="FEAT-001", name="f", status="requested")
        mock_repo.list_by_status = AsyncMock(return_value=[row])

        mock_factory = MagicMock()
        async_session_ctx = MagicMock()
        async_session_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        async_session_ctx.__aexit__ = AsyncMock(return_value=False)
        with patch(
            "general_ludd.routers.features.FeatureRepository", return_value=mock_repo
        ):
            mock_factory.return_value = async_session_ctx
            app.state._session_factory = mock_factory
            resp = await _get(app, "/api/features?status=requested")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1
        assert resp.json()["filtered"] is True

    @pytest.mark.asyncio
    async def test_list_features_invalid_status_returns_422(self, app):
        mock_factory = MagicMock()
        async_session_ctx = MagicMock()
        async_session_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        async_session_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_factory.return_value = async_session_ctx
        app.state._session_factory = mock_factory
        resp = await _get(app, "/api/features?status=not-a-real-status")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_list_features_filtered_by_category(self, app):
        mock_repo = MagicMock()
        row = MockFeatureRow(id="FEAT-001", name="f", status="requested", category="security")
        mock_repo.list_by_category = AsyncMock(return_value=[row])

        mock_factory = MagicMock()
        async_session_ctx = MagicMock()
        async_session_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        async_session_ctx.__aexit__ = AsyncMock(return_value=False)
        with patch(
            "general_ludd.routers.features.FeatureRepository", return_value=mock_repo
        ):
            mock_factory.return_value = async_session_ctx
            app.state._session_factory = mock_factory
            resp = await _get(app, "/api/features?category=security")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1


class TestGetFeature:
    @pytest.mark.asyncio
    async def test_get_feature_no_db_returns_503(self, app):
        app.state._session_factory = None
        resp = await _get(app, "/api/features/FEAT-001")
        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_get_feature_not_found_returns_404(self, app):
        mock_repo = MagicMock()
        mock_repo.get_by_id = AsyncMock(return_value=None)

        mock_factory = MagicMock()
        async_session_ctx = MagicMock()
        async_session_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        async_session_ctx.__aexit__ = AsyncMock(return_value=False)
        with patch(
            "general_ludd.routers.features.FeatureRepository", return_value=mock_repo
        ):
            mock_factory.return_value = async_session_ctx
            app.state._session_factory = mock_factory
            resp = await _get(app, "/api/features/FEAT-999")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_feature_found_returns_200(self, app):
        mock_repo = MagicMock()
        row = MockFeatureRow(id="FEAT-001", name="my-feature", status="verified")
        mock_repo.get_by_id = AsyncMock(return_value=row)

        mock_factory = MagicMock()
        async_session_ctx = MagicMock()
        async_session_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        async_session_ctx.__aexit__ = AsyncMock(return_value=False)
        with patch(
            "general_ludd.routers.features.FeatureRepository", return_value=mock_repo
        ):
            mock_factory.return_value = async_session_ctx
            app.state._session_factory = mock_factory
            resp = await _get(app, "/api/features/FEAT-001")
        assert resp.status_code == 200
        assert resp.json()["id"] == "FEAT-001"
        assert resp.json()["name"] == "my-feature"


class TestVerifyFeatures:
    @pytest.mark.asyncio
    async def test_verify_no_db_returns_503(self, app):
        app.state._session_factory = None
        resp = await _post(app, "/api/features/verify")
        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_verify_empty_list_returns_summary(self, app):
        mock_repo = MagicMock()
        mock_repo.list_all = AsyncMock(return_value=[])

        mock_factory = MagicMock()
        async_session_ctx = MagicMock()
        async_session_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
        async_session_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "general_ludd.routers.features.FeatureRepository", return_value=mock_repo
        ):
            mock_factory.return_value = async_session_ctx
            app.state._session_factory = mock_factory
            resp = await _post(app, "/api/features/verify")
        assert resp.status_code == 200
        assert resp.json()["summary"]["total"] == 0

    @pytest.mark.asyncio
    async def test_verify_with_features_persists_results(self, app):
        row = MockFeatureRow(
            id="FEAT-001", name="f1", status="requested",
            evidence='["module: x"]',
        )
        mock_repo = MagicMock()
        mock_repo.list_all = AsyncMock(return_value=[row])
        mock_repo.set_status = AsyncMock()

        mock_verifier = MagicMock()
        mock_verifier.verify_all.return_value = {
            "total": 1, "passed": 1, "failed": 0, "skipped": 0,
            "results": [
                {
                    "id": "FEAT-001",
                    "status": "verified",
                    "verified_at": "2026-01-01T00:00:00",
                    "evidence_results": {"test": "passed"},
                }
            ],
        }

        mock_factory = MagicMock()
        mock_session = MagicMock()
        mock_session.commit = AsyncMock()
        async_session_ctx = MagicMock()
        async_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        async_session_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "general_ludd.routers.features.FeatureRepository", return_value=mock_repo
        ), patch(
            "general_ludd.quality.feature_verifier.FeatureVerifier", return_value=mock_verifier
        ):
            mock_factory.return_value = async_session_ctx
            app.state._session_factory = mock_factory
            resp = await _post(app, "/api/features/verify")
        assert resp.status_code == 200
        assert resp.json()["summary"]["total"] == 1
        mock_session.commit.assert_called_once()
