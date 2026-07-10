"""Endpoint tests for routers/eval.py (G2 eval router).

``routers/eval.py`` registers ``POST /admin/eval/run`` and
``GET /admin/eval/results``. It is registered directly by ``daemon.py`` (see
``daemon.py``'s ``create_daemon_app`` router-import block, ``eval_router.
register(app, daemon_state)``) and — as of this change — also by
``routers/__init__.register_all`` (the parallel contract pinned by
``tests/unit/test_router_registration.py``).

Existing coverage (``tests/integration/test_g2_eval_daemon_wiring.py``,
``tests/integration/test_eval_harness.py``) exercises these endpoints through
the full ``create_daemon_app()`` stack. This file adds router-level unit
coverage — a bare FastAPI app with only ``routers.eval.register`` applied —
mirroring the convention in ``test_observe_router.py``:

  - POST /admin/eval/run      -> happy path with a fake evaluator wired
  - GET  /admin/eval/results  -> last_results survives across requests
  - Empty-state degradation: no ``app.state.eval_harness`` -> 503 (not 500);
    a harness with no evaluator wired -> 503 "no evaluator configured"
  - Validation: empty ``cases`` -> 422; a case missing ``id`` -> 422
  - Auth posture: the router registers no public bypass — a PSK-style gate
    wrapped around it (mirroring test_observe_auth_posture.py) must refuse
    unauthenticated calls and admit correctly-authenticated ones. Real PSK
    enforcement is the daemon middleware's job; this proves the router does
    not short-circuit it.
"""

from __future__ import annotations

import hmac
from typing import ClassVar
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from general_ludd.eval.harness import EvalHarness
from general_ludd.routers.eval import register

_CASE = {
    "id": "case-1",
    "description": "test case",
    "input_files": {"main.py": "old"},
    "expected_patch": "def foo():\n    return 42\n",
    "task_type": "code",
    "assertions": {},
}


def _fake_evaluator(patch: str = "def foo():\n    return 42\n") -> MagicMock:
    evaluator = MagicMock()
    evaluator.generate_patch.return_value = patch
    return evaluator


@pytest.fixture
def app_with_harness() -> FastAPI:
    app = FastAPI()
    app.state.eval_harness = EvalHarness(model="sonnet", evaluator=_fake_evaluator())
    register(app, {})
    return app


@pytest.fixture
def client_with_harness(app_with_harness: FastAPI) -> TestClient:
    return TestClient(app_with_harness)


class TestRunHappyPath:
    def test_run_returns_results_for_each_case(
        self, client_with_harness: TestClient
    ) -> None:
        resp = client_with_harness.post("/admin/eval/run", json={"cases": [_CASE]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["run"] is True
        assert data["total"] == 1
        assert len(data["results"]) == 1
        result = data["results"][0]
        assert result["case_id"] == "case-1"
        assert result["passed"] is True
        assert isinstance(result["score"], (int, float))
        assert isinstance(result["duration_ms"], int)
        assert isinstance(result["errors"], list)

    def test_run_with_multiple_cases(self, client_with_harness: TestClient) -> None:
        case_b = dict(_CASE, id="case-2")
        resp = client_with_harness.post(
            "/admin/eval/run", json={"cases": [_CASE, case_b]}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert {r["case_id"] for r in data["results"]} == {"case-1", "case-2"}


class TestResultsEndpoint:
    def test_results_returns_last_run(self, client_with_harness: TestClient) -> None:
        client_with_harness.post("/admin/eval/run", json={"cases": [_CASE]})
        resp = client_with_harness.get("/admin/eval/results")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["results"][0]["case_id"] == "case-1"

    def test_results_empty_before_any_run(self) -> None:
        app = FastAPI()
        app.state.eval_harness = EvalHarness(model="sonnet", evaluator=_fake_evaluator())
        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/eval/results")
        assert resp.status_code == 200
        assert resp.json() == {"total": 0, "passed": 0, "results": []}


class TestValidation:
    def test_run_with_empty_cases_is_422(self, client_with_harness: TestClient) -> None:
        resp = client_with_harness.post("/admin/eval/run", json={"cases": []})
        assert resp.status_code == 422

    def test_run_with_missing_id_field_is_422(
        self, client_with_harness: TestClient
    ) -> None:
        bad_case = {k: v for k, v in _CASE.items() if k != "id"}
        resp = client_with_harness.post("/admin/eval/run", json={"cases": [bad_case]})
        assert resp.status_code == 422


class TestEmptyStateDegradation:
    def test_run_without_harness_returns_503(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.post("/admin/eval/run", json={"cases": [_CASE]})
        assert resp.status_code == 503

    def test_results_without_harness_returns_503(self) -> None:
        app = FastAPI()
        register(app, {})
        client = TestClient(app)
        resp = client.get("/admin/eval/results")
        assert resp.status_code == 503

    def test_run_with_harness_but_no_evaluator_returns_503(self) -> None:
        app = FastAPI()
        app.state.eval_harness = EvalHarness(model="sonnet")  # no evaluator
        register(app, {})
        client = TestClient(app)
        resp = client.post("/admin/eval/run", json={"cases": [_CASE]})
        assert resp.status_code == 503

    def test_register_on_bare_app_and_empty_state_does_not_crash(self) -> None:
        """Mirrors the generic contract in test_router_registration.py:
        ``register(app, {})`` must add >=1 route without raising."""
        app = FastAPI()
        before = len(app.routes)
        register(app, {})
        assert len(app.routes) > before


_PSK = "unit-test-psk-eval"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Deliberately empty for eval paths — mirrors the daemon not listing any
# /admin/eval/* path in _PUBLIC_PATHS.
_PUBLIC_PATHS: set[str] = {"/healthz"}


def _app_with_psk_gate() -> FastAPI:
    app = FastAPI()
    app.state.eval_harness = EvalHarness(model="sonnet", evaluator=_fake_evaluator())
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


class TestEvalIsPskGated:
    """Mirrors test_observe_auth_posture.py: the router registers no public
    bypass, so a PSK-style gate wrapped around it must refuse unauthenticated
    calls and allow correctly-authenticated ones."""

    _CASES: ClassVar[list[tuple[str, str, dict[str, object] | None]]] = [
        ("POST", "/admin/eval/run", {"cases": [_CASE]}),
        ("GET", "/admin/eval/results", None),
    ]

    @pytest.mark.parametrize("method,path,body", _CASES)
    def test_unauthenticated_is_refused(self, method: str, path: str, body) -> None:
        client = TestClient(_app_with_psk_gate())
        resp = client.request(method, path, json=body)
        assert resp.status_code == 401

    @pytest.mark.parametrize("method,path,body", _CASES)
    def test_with_psk_succeeds(self, method: str, path: str, body) -> None:
        client = TestClient(_app_with_psk_gate())
        resp = client.request(
            method, path, json=body, headers={"Authorization": f"Bearer {_PSK}"}
        )
        assert resp.status_code == 200
