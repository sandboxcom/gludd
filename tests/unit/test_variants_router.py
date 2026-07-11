"""Endpoint tests for routers/variants.py.

GET /admin/prompts/variants/report — the single endpoint.
This is an admin path (not in _PUBLIC_PATHS) and relies on the daemon's
auth middleware for PSK enforcement — the router itself does NOT re-check.
"""

from __future__ import annotations

import hmac

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from general_ludd.routers.variants import register

_PSK = "unit-test-psk-variants"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_PUBLIC_PATHS: set[str] = {"/healthz"}


def _app_with_psk_gate() -> FastAPI:
    app = FastAPI()
    register(app, {})

    def _is_public(method: str, path: str) -> bool:
        if method.upper() not in _SAFE_METHODS:
            return False
        return path in _PUBLIC_PATHS

    @app.middleware("http")
    async def _auth(request, call_next):
        if not _is_public(request.method, request.url.path):
            auth = request.headers.get("Authorization", "")
            token = (
                auth.removeprefix("Bearer ").strip()
                if auth.startswith("Bearer ")
                else ""
            )
            if not token or not hmac.compare_digest(token, _PSK):
                return JSONResponse(status_code=401, content={"error": "unauthorized"})
        return await call_next(request)

    return app


@pytest.fixture
def app() -> FastAPI:
    """Bare app (no auth middleware) for testing router logic directly."""
    _app = FastAPI()
    register(_app, {})
    return _app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# Auth posture (PSK gate)
# ---------------------------------------------------------------------------

class TestVariantReportAuthPosture:
    def test_variant_report_requires_auth(self) -> None:
        client = TestClient(_app_with_psk_gate())
        resp = client.get("/admin/prompts/variants/report")
        assert resp.status_code == 401

    def test_variant_report_with_valid_psk_reaches_handler(self) -> None:
        client = TestClient(_app_with_psk_gate())
        resp = client.get(
            "/admin/prompts/variants/report",
            headers={"Authorization": f"Bearer {_PSK}"},
        )
        assert resp.status_code != 401, resp.text
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Happy path — bare app (no EventLoop wired)
# ---------------------------------------------------------------------------

class TestVariantReportHappyPath:
    GET = "/admin/prompts/variants/report"

    def test_returns_expected_shape_when_event_loop_not_running(
        self, client: TestClient
    ) -> None:
        resp = client.get(self.GET)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
        assert "templates" in data
        assert "template_count" in data
        assert "note" in data
        assert data["note"] == "EventLoop not running"

    def test_templates_is_empty_when_no_event_loop(self, client: TestClient) -> None:
        resp = client.get(self.GET)
        data = resp.json()
        assert data["templates"] == {}
        assert data["template_count"] == 0


# ---------------------------------------------------------------------------
# With EventLoop wired but no PromptVariantSelector
# ---------------------------------------------------------------------------

class DummyEventLoop:
    pass


class TestVariantReportWithoutSelector:
    GET = "/admin/prompts/variants/report"

    @pytest.fixture
    def app(self) -> FastAPI:
        _app = FastAPI()
        _app.state.event_loop = DummyEventLoop()
        register(_app, {})
        return _app

    @pytest.fixture
    def client(self, app: FastAPI) -> TestClient:
        return TestClient(app)

    def test_reports_selector_not_wired(self, client: TestClient) -> None:
        resp = client.get(self.GET)
        assert resp.status_code == 200
        data = resp.json()
        assert data["note"] == "PromptVariantSelector not wired"
        assert data["templates"] == {}
        assert data["template_count"] == 0


# ---------------------------------------------------------------------------
# With PromptVariantSelector wired but no VariantMetrics
# ---------------------------------------------------------------------------

class DummySelector:
    pass


class TestVariantReportWithoutMetrics:
    GET = "/admin/prompts/variants/report"

    @pytest.fixture
    def app(self) -> FastAPI:
        _app = FastAPI()
        _app.state.event_loop = DummyEventLoop()
        _app.state.event_loop._prompt_variant_selector = DummySelector()
        register(_app, {})
        return _app

    @pytest.fixture
    def client(self, app: FastAPI) -> TestClient:
        return TestClient(app)

    def test_reports_metrics_not_wired(self, client: TestClient) -> None:
        resp = client.get(self.GET)
        assert resp.status_code == 200
        data = resp.json()
        assert data["note"] == "VariantMetrics not wired"
        assert data["templates"] == {}
        assert data["template_count"] == 0


# ---------------------------------------------------------------------------
# With full wiring — VariantMetrics.generate_variant_report()
# ---------------------------------------------------------------------------

class TestVariantReportWithMetrics:
    GET = "/admin/prompts/variants/report"

    @pytest.fixture
    def app(self) -> FastAPI:
        _app = FastAPI()
        _app.state.event_loop = DummyEventLoop()
        selector = DummySelector()
        selector.variant_metrics = DummySelector()
        selector.variant_metrics.generate_variant_report = lambda: {
            "templates": {"tmpl-001": {"variant_a": 0.73, "variant_b": 0.68}},
            "template_count": 1,
        }
        _app.state.event_loop._prompt_variant_selector = selector
        register(_app, {})
        return _app

    @pytest.fixture
    def client(self, app: FastAPI) -> TestClient:
        return TestClient(app)

    def test_returns_report_data(self, client: TestClient) -> None:
        resp = client.get(self.GET)
        assert resp.status_code == 200
        data = resp.json()
        assert data["templates"]["tmpl-001"]["variant_a"] == 0.73
        assert data["template_count"] == 1

    def test_report_shape_matches_expected(self, client: TestClient) -> None:
        resp = client.get(self.GET)
        data = resp.json()
        assert "templates" in data
        assert isinstance(data["templates"], dict)
        assert isinstance(data["template_count"], int)


# ---------------------------------------------------------------------------
# Register contract
# ---------------------------------------------------------------------------

class TestRegister:
    def test_register_on_bare_app_adds_routes(self) -> None:
        app = FastAPI()
        before = len(app.routes)
        register(app, {})
        assert len(app.routes) > before
