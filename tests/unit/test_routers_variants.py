"""Deep behavioral tests for routers/variants.py — PromptVariant A/B report endpoint."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient


class TestVariantsModuleShape:
    def test_register_is_callable(self) -> None:
        from general_ludd.routers.variants import register

        assert callable(register)

    def test_register_returns_none(self) -> None:
        from general_ludd.routers.variants import register

        result = register(FastAPI(), {})
        assert result is None

    def test_register_adds_expected_path(self) -> None:
        from general_ludd.routers.variants import register

        app = FastAPI()
        register(app, {})
        paths = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/admin/prompts/variants/report" in paths


class TestVariantsReportGracefulDegradation:
    def test_no_event_loop_returns_empty_with_note(self) -> None:
        from general_ludd.routers.variants import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/prompts/variants/report")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"templates": {}, "template_count": 0, "note": "EventLoop not running"}

    def test_event_loop_without_selector_returns_note(self) -> None:
        from general_ludd.routers.variants import register

        app = FastAPI()
        app.state.event_loop = MagicMock()
        del app.state.event_loop._prompt_variant_selector
        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/prompts/variants/report")
        data = resp.json()
        assert data["template_count"] == 0
        assert data["note"] == "PromptVariantSelector not wired"

    def test_selector_without_metrics_returns_note(self) -> None:
        from general_ludd.routers.variants import register

        app = FastAPI()
        app.state.event_loop = MagicMock()
        app.state.event_loop._prompt_variant_selector = MagicMock()
        del app.state.event_loop._prompt_variant_selector.variant_metrics
        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/prompts/variants/report")
        data = resp.json()
        assert data["template_count"] == 0
        assert data["note"] == "VariantMetrics not wired"

    def test_metrics_exception_returns_empty_with_error(self) -> None:
        from general_ludd.routers.variants import register

        app = FastAPI()
        app.state.event_loop = MagicMock()
        selector = MagicMock()
        selector.variant_metrics.generate_variant_report.side_effect = RuntimeError("boom")
        app.state.event_loop._prompt_variant_selector = selector
        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/prompts/variants/report")
        data = resp.json()
        assert data["template_count"] == 0
        assert "error" in data
        assert data["error"] == "Report generation failed"


class TestVariantsReportSuccess:
    def test_returns_metrics_report_when_wired(self) -> None:
        from general_ludd.routers.variants import register

        expected = {"templates": {"t1": {"winner": "A"}}, "template_count": 1}
        app = FastAPI()
        app.state.event_loop = MagicMock()
        selector = MagicMock()
        selector.variant_metrics.generate_variant_report.return_value = expected
        app.state.event_loop._prompt_variant_selector = selector
        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/prompts/variants/report")
        assert resp.status_code == 200
        assert resp.json() == expected

    def test_is_get_only(self) -> None:
        from general_ludd.routers.variants import register

        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.post("/admin/prompts/variants/report", json={})
        assert resp.status_code == 405

    def test_event_loop_retrieved_from_app_state_label(self) -> None:
        from general_ludd.routers.variants import register

        app = FastAPI()
        app.state.event_loop = MagicMock()
        app.state.event_loop._prompt_variant_selector = MagicMock()
        app.state.event_loop._prompt_variant_selector.variant_metrics = MagicMock()
        app.state.event_loop._prompt_variant_selector.variant_metrics.generate_variant_report.return_value = {
            "templates": {"x": {}},
            "template_count": 1,
        }
        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/prompts/variants/report")
        assert resp.status_code == 200
        assert resp.json()["template_count"] == 1
