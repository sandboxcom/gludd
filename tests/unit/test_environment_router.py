"""GET /api/environment: contract, secret-leak protection, and PSK gating.

The environment endpoint is the model's single window into the environment it
runs inside. This test proves:
  - the consolidated brief returns 200 with every top-level section present;
  - the model roster NEVER carries a credential/secret VALUE (the load-bearing
    security assertion: no api key / token / secret / password leaks through);
  - the endpoint sits behind the daemon PSK middleware exactly like /api/facts
    (a request with no PSK is refused; the same request with a Bearer PSK
    succeeds).
"""

from __future__ import annotations

import hmac
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from general_ludd.config.model_routing import ModelRoutingConfig
from general_ludd.models.gateway import ModelGateway, ModelProfile
from general_ludd.routers.environment import register

_PSK = "env-unit-test-psk"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_PUBLIC_PATHS: set[str] = {"/healthz"}

# A credential value that must NEVER appear anywhere in the response.
_SECRET_VALUE = "sk-super-secret-key-DO-NOT-LEAK-0123456789"


def _gateway_with_secret() -> ModelGateway:
    profile = ModelProfile(
        model_profile_id="flagship",
        provider="openai",
        model_name="glm-4.6",
        # Secret-bearing fields that MUST be filtered out of the roster.
        credential_alias=_SECRET_VALUE,
        api_base_alias="https://" + _SECRET_VALUE + ".invalid",
        enabled=True,
        quality_class="high",
        latency_class="standard",
        api_metered=True,
        cost_per_input_token=5e-6,
        cost_per_output_token=15e-6,
        fallback_profiles=["weak"],
    )
    weak = ModelProfile(
        model_profile_id="weak",
        provider="openai",
        model_name="glm-4.5-air",
        enabled=True,
        api_metered=False,
    )
    return ModelGateway(profiles=[profile, weak])


def _app_with_psk_gate() -> FastAPI:
    app = FastAPI()
    app.state._model_gateway = _gateway_with_secret()
    app.state._startup_config = {
        "model_routing": ModelRoutingConfig(
            default_profile="flagship",
            weak_model_profile="weak",
            role_routing={"reviewer": "flagship"},
            fallback_chain=["flagship", "weak"],
        )
    }
    app.state._budget_guard = None
    app.state._spend_limiter = None
    app.state._session_factory = None
    app.state._mcp_client = None
    app.state._skill_registry = None
    app.state._compute_config = None
    register(app, {})

    def _is_public(method: str, path: str) -> bool:
        if method.upper() not in _SAFE_METHODS:
            return False
        return path in _PUBLIC_PATHS

    @app.middleware("http")
    async def _auth(request: Any, call_next: Any) -> Any:
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
def client() -> TestClient:
    return TestClient(_app_with_psk_gate())


def test_unauthenticated_is_refused(client: TestClient) -> None:
    resp = client.get("/api/environment")
    assert resp.status_code == 401


def test_with_psk_returns_brief(client: TestClient) -> None:
    resp = client.get(
        "/api/environment", headers={"Authorization": f"Bearer {_PSK}"}
    )
    assert resp.status_code == 200
    body = resp.json()
    for key in (
        "models",
        "routing",
        "budget",
        "compute",
        "tools",
        "skills",
        "queues",
        "system",
        "optimization",
    ):
        assert key in body, f"missing top-level section: {key}"

    # Roster carries the characteristic fields, mapped to the contract key names.
    roster = body["models"]
    assert any(m["profile_id"] == "flagship" for m in roster)
    flagship = next(m for m in roster if m["profile_id"] == "flagship")
    assert flagship["model"] == "glm-4.6"
    assert flagship["enabled"] is True
    assert "fallback_profiles" in flagship

    # Routing + optimization plumbed through.
    assert body["routing"]["default_profile"] == "flagship"
    assert body["routing"]["weak_model_profile"] == "weak"
    assert "recommended_profile_for" in body["optimization"]


def test_no_secret_leaks_anywhere_in_response(client: TestClient) -> None:
    """The load-bearing security assertion: no credential VALUE and no secret
    FIELD name carrying a real value appears anywhere in the serialized brief.
    """
    resp = client.get(
        "/api/environment", headers={"Authorization": f"Bearer {_PSK}"}
    )
    assert resp.status_code == 200
    raw = resp.text

    # 1) The actual secret value must never appear.
    assert _SECRET_VALUE not in raw, "credential value leaked into /api/environment"

    # 2) No credential-bearing ModelProfile field name should appear in the
    #    roster (these are the fields that carry secret references).
    for m in resp.json()["models"]:
        for forbidden in (
            "credential_alias",
            "api_base_alias",
            "api_key",
            "token",
            "secret",
            "password",
            "psk",
        ):
            assert forbidden not in m, (
                f"roster entry leaked a credential field: {forbidden} in {m!r}"
            )


# ---------------------------------------------------------------------------
# GET /api/environment/advise — per-task advice surface
# ---------------------------------------------------------------------------


def _advise(client: TestClient, **params: Any) -> dict[str, Any]:
    """GET /api/environment/advise with the PSK and return the JSON body."""
    resp = client.get(
        "/api/environment/advise",
        params=params,
        headers={"Authorization": f"Bearer {_PSK}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_advise_unauthenticated_is_refused(client: TestClient) -> None:
    resp = client.get("/api/environment/advise", params={"work_type": "feature"})
    assert resp.status_code == 401


def test_advise_returns_contract_for_feature(client: TestClient) -> None:
    body = _advise(client, work_type="feature", prompt_tokens=2000)
    # Top-level contract keys.
    for key in (
        "task_type",
        "recommendation",
        "route",
        "est_cost_usd",
        "use_workflow",
        "workflow_reason",
        "resource_hints",
    ):
        assert key in body, f"missing advise key: {key}"
    assert body["task_type"] == "feature"
    # recommendation carries model_profile + the classes.
    rec = body["recommendation"]
    assert "model_profile" in rec
    for sub in (
        "reason",
        "composite_score",
        "fallback",
        "sample_count",
        "latency_class",
        "quality_class",
    ):
        assert sub in rec
    # route + cost + hints contract.
    assert "selected_model_profile_id" in body["route"]
    assert "selected_prompt_profile_id" in body["route"]
    assert isinstance(body["est_cost_usd"], float)
    for hint in ("prefer_local", "budget_ok", "budget_warning", "context_fits"):
        assert hint in body["resource_hints"]


def test_advise_use_workflow_true_for_feature(client: TestClient) -> None:
    body = _advise(client, work_type="feature")
    # A gateway is wired and budget is unconstrained -> workflow recommended.
    assert body["use_workflow"] is True
    assert body["workflow_reason"]


def test_advise_use_workflow_false_for_docs(client: TestClient) -> None:
    body = _advise(client, work_type="docs")
    assert body["use_workflow"] is False
    assert "docs" in body["workflow_reason"]


def test_advise_rejects_negative_prompt_tokens(client: TestClient) -> None:
    """A negative prompt_tokens would yield a negative est_cost and silently
    suppress the budget warning; the ge=0 Query bound rejects it with a 422.

    422 is request-validation (distinct from the never-500 server-error
    contract), and it fires before the handler so the fallback path is bypassed.
    """
    resp = client.get(
        "/api/environment/advise",
        params={"work_type": "feature", "prompt_tokens": -5},
        headers={"Authorization": f"Bearer {_PSK}"},
    )
    assert resp.status_code == 422


def test_advise_rejects_oversized_work_type(client: TestClient) -> None:
    """work_type is reflected into the response (task_type / workflow_reason);
    an over-long string is rejected rather than echoed back unbounded."""
    resp = client.get(
        "/api/environment/advise",
        params={"work_type": "x" * 200},
        headers={"Authorization": f"Bearer {_PSK}"},
    )
    assert resp.status_code == 422


def test_advise_never_500_on_bare_app() -> None:
    """A FastAPI app with NO wired state must still return a 200 fallback."""
    app = FastAPI()
    register(app, {})  # no app.state deps wired at all
    bare = TestClient(app)
    resp = bare.get("/api/environment/advise", params={"work_type": "feature"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Fallback recommendation: never raises, never 500s.
    assert body["task_type"] == "feature"
    assert "recommendation" in body
    assert "model_profile" in body["recommendation"]
    assert isinstance(body["est_cost_usd"], float)


# ---------------------------------------------------------------------------
# optimization.hints — burn_rate + model_flaky surfaced through the live route
# ---------------------------------------------------------------------------


class _StubBudgetGuard:
    """RunBudgetGuard duck-type the budget facet reads (spend/elapsed/remaining)."""

    def __init__(self, spent: float, remaining: float, elapsed: float):
        self._spent = spent
        self._remaining = remaining
        self._elapsed = elapsed

    def get_total_spend(self) -> float:
        return self._spent

    def get_elapsed_seconds(self) -> float:
        return self._elapsed

    def check_run_budget(self) -> dict[str, Any]:
        return {"remaining_budget": self._remaining}


class _StubMetricsCollector:
    """MetricsCollector duck-type for the model_flaky route test."""

    def __init__(self, flaky: set[str], failures: dict[str, list[str]]):
        self._flaky = flaky
        self._failures = failures

    def is_flaky(self, model_profile_id: str, *a: Any, **k: Any) -> bool:
        return model_profile_id in self._flaky

    def get_recent_failures(
        self, model_profile_id: str, *a: Any, **k: Any
    ) -> list[str]:
        return list(self._failures.get(model_profile_id, []))


def _app_with_signals(
    *, budget_guard: Any = None, metrics_collector: Any = None
) -> FastAPI:
    app = _app_with_psk_gate()
    app.state._budget_guard = budget_guard
    app.state._metrics_collector = metrics_collector
    return app


def _optimization(client: TestClient) -> dict[str, Any]:
    resp = client.get(
        "/api/environment", headers={"Authorization": f"Bearer {_PSK}"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["optimization"]


def test_environment_surfaces_burn_rate_hint() -> None:
    # spent 9.0 / 90s -> 0.1 USD/s; remaining 1.0 -> ~10s left -> warning.
    guard = _StubBudgetGuard(spent=9.0, remaining=1.0, elapsed=90.0)
    client = TestClient(_app_with_signals(budget_guard=guard))
    opt = _optimization(client)
    burn = [h for h in opt["hints"] if h["signal"] == "burn_rate"]
    assert len(burn) == 1
    assert burn[0]["velocity_usd_per_sec"] == pytest.approx(0.1)
    assert burn[0]["time_remaining_sec"] is not None
    assert burn[0]["severity"] == "warning"


def test_environment_surfaces_model_flaky_hint() -> None:
    collector = _StubMetricsCollector(
        flaky={"flagship"},
        failures={"flagship": ["timeout", "500", "boom", "extra"]},
    )
    client = TestClient(_app_with_signals(metrics_collector=collector))
    opt = _optimization(client)
    flaky = [h for h in opt["hints"] if h["signal"] == "model_flaky"]
    assert len(flaky) == 1
    assert flaky[0]["model"] == "flagship"
    assert flaky[0]["severity"] == "warning"
    assert flaky[0]["recent_failures"] == ["timeout", "500", "boom"]


def test_environment_no_model_flaky_when_collector_absent() -> None:
    # The default PSK app sets _metrics_collector = None implicitly (not wired).
    client = TestClient(_app_with_signals(metrics_collector=None))
    opt = _optimization(client)
    assert [h for h in opt["hints"] if h["signal"] == "model_flaky"] == []
