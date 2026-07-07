"""Unit tests for routers/quantization.py router endpoint handlers.

Covers the previously 19.4%-rated module by exercising:
  * GET /admin/quantization (list, empty)
  * POST /admin/quantization/detect (no tracker, with tracker, model_id required)
  * GET /admin/quantization/{model_id} (no tracker, found, not found)
  * POST /admin/quantization/drift-check (no tracker, with data)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from general_ludd.daemon import create_daemon_app


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


class TestListQuantization:
    @pytest.mark.asyncio
    async def test_list_no_tracker_returns_empty(self, app):
        app.state._quantization_tracker = None
        resp = await _get(app, "/admin/quantization")
        assert resp.status_code == 200
        assert resp.json() == {"models": []}

    @pytest.mark.asyncio
    async def test_list_with_tracker_returns_models(self, app):
        mock_tracker = MagicMock()
        mock_tracker.to_dict.return_value = {"gptq-model": {"precision": "int4"}}
        app.state._quantization_tracker = mock_tracker
        resp = await _get(app, "/admin/quantization")
        assert resp.status_code == 200
        assert resp.json()["models"] == {"gptq-model": {"precision": "int4"}}


class TestDetectQuantization:
    @pytest.mark.asyncio
    async def test_detect_no_model_id_returns_422(self, app):
        resp = await _post(app, "/admin/quantization/detect", json={})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_detect_creates_tracker_if_missing(self, app):
        app.state._quantization_tracker = None

        mock_hf = MagicMock()
        mock_hf.detect = AsyncMock(return_value=[])
        mock_fw = MagicMock()
        mock_fw.detect = AsyncMock(return_value=[])
        mock_or = MagicMock()
        mock_or.detect = AsyncMock(return_value=[])
        mock_probe = MagicMock()
        mock_probe.arithmetic_probe_prompt = MagicMock(return_value="probe")

        with patch(
            "general_ludd.routers.quantization.HuggingFaceDetector", return_value=mock_hf
        ), patch(
            "general_ludd.routers.quantization.FireworksDetector", return_value=mock_fw
        ), patch(
            "general_ludd.routers.quantization.OpenRouterEndpointDetector", return_value=mock_or
        ), patch(
            "general_ludd.routers.quantization.SelfProbeDetector", return_value=mock_probe
        ):
            resp = await _post(
                app, "/admin/quantization/detect", json={"model_id": "test-model"}
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["model_id"] == "test-model"
        assert data["sources_checked"] == 0

    @pytest.mark.asyncio
    async def test_detect_with_results_populates_fields(self, app):
        mock_tracker = MagicMock()
        mock_tracker.check_drift.return_value = False
        mock_tracker.get.return_value = MagicMock(
            precision="fp16", source="huggingface", confidence=0.9
        )
        app.state._quantization_tracker = mock_tracker

        fake_info = MagicMock(
            precision="fp16", source="huggingface", confidence=0.9,
            provider_name="HF",
        )
        mock_hf = MagicMock()
        mock_hf.detect = AsyncMock(return_value=[fake_info])
        mock_fw = MagicMock()
        mock_fw.detect = AsyncMock(return_value=[])
        mock_or = MagicMock()
        mock_or.detect = AsyncMock(return_value=[])
        mock_probe = MagicMock()
        mock_probe.arithmetic_probe_prompt = MagicMock(return_value="probe prompt text")

        with patch(
            "general_ludd.routers.quantization.HuggingFaceDetector", return_value=mock_hf
        ), patch(
            "general_ludd.routers.quantization.FireworksDetector", return_value=mock_fw
        ), patch(
            "general_ludd.routers.quantization.OpenRouterEndpointDetector", return_value=mock_or
        ), patch(
            "general_ludd.routers.quantization.SelfProbeDetector", return_value=mock_probe
        ):
            resp = await _post(
                app, "/admin/quantization/detect", json={"model_id": "test-model"}
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["sources_checked"] == 1
        assert data["self_probe_prompt"] == "probe prompt text"
        assert data["best"] is not None


class TestGetQuantization:
    @pytest.mark.asyncio
    async def test_get_no_tracker_returns_none_precision(self, app):
        app.state._quantization_tracker = None
        resp = await _get(app, "/admin/quantization/test-model")
        assert resp.status_code == 200
        assert resp.json()["precision"] is None

    @pytest.mark.asyncio
    async def test_get_not_found_returns_none_precision(self, app):
        mock_tracker = MagicMock()
        mock_tracker.get.return_value = None
        app.state._quantization_tracker = mock_tracker
        resp = await _get(app, "/admin/quantization/nonexistent")
        assert resp.status_code == 200
        assert resp.json()["precision"] is None

    @pytest.mark.asyncio
    async def test_get_found_returns_info(self, app):
        mock_info = MagicMock()
        mock_info.precision = "int8"
        mock_info.source = "fireworks"
        mock_info.confidence = 0.85
        mock_info.provider_name = "FW"
        mock_info.bits_estimate = 8
        mock_info.detected_at = "2026-01-01T00:00:00Z"
        mock_tracker = MagicMock()
        mock_tracker.get.return_value = mock_info
        app.state._quantization_tracker = mock_tracker
        resp = await _get(app, "/admin/quantization/gptq-model")
        assert resp.status_code == 200
        data = resp.json()
        assert data["precision"] == "int8"
        assert data["source"] == "fireworks"
        assert data["confidence"] == 0.85


class TestDriftCheckQuantization:
    @pytest.mark.asyncio
    async def test_drift_check_no_tracker_returns_empty(self, app):
        app.state._quantization_tracker = None
        resp = await _post(app, "/admin/quantization/drift-check")
        assert resp.status_code == 200
        assert resp.json() == {"drift_detected": False, "changes": []}

    @pytest.mark.asyncio
    async def test_drift_check_with_no_models(self, app):
        mock_tracker = MagicMock()
        mock_tracker.list_all.return_value = {}
        app.state._quantization_tracker = mock_tracker
        resp = await _post(app, "/admin/quantization/drift-check")
        assert resp.status_code == 200
        assert resp.json()["drift_detected"] is False

    @pytest.mark.asyncio
    async def test_drift_check_with_drift_detected(self, app):
        mock_tracker = MagicMock()
        mock_tracker.list_all.return_value = {"model-a": MagicMock()}
        mock_tracker.check_drift.return_value = True
        old_info = MagicMock()
        old_info.precision = "fp16"
        mock_tracker.get.return_value = old_info

        fake_info = MagicMock(precision="int4", source="fireworks")
        mock_hf = MagicMock()
        mock_hf.detect = AsyncMock(return_value=[])
        mock_or = MagicMock()
        mock_or.detect = AsyncMock(return_value=[fake_info])

        app.state._quantization_tracker = mock_tracker
        with patch(
            "general_ludd.routers.quantization.HuggingFaceDetector", return_value=mock_hf
        ), patch(
            "general_ludd.routers.quantization.OpenRouterEndpointDetector", return_value=mock_or
        ):
            resp = await _post(app, "/admin/quantization/drift-check")
        assert resp.status_code == 200
        data = resp.json()
        assert data["drift_detected"] is True
        assert len(data["changes"]) == 1
