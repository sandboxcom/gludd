"""E2E tests for Azure backend (env-pointer path).

Points gludd at an already-running Azure endpoint (Azure VM hosting
vllm/ollama/llama.cpp behind an OpenAI-compatible /v1, or Azure OpenAI).

Env: AZURE_BASE_URL, AZURE_MODEL, AZURE_API_KEY (or AZURE_OPENAI_API_KEY).
Optional: AZURE_BACKEND_KIND in {vllm,ollama,llamacpp,azure_openai}.

Skip when AZURE_BASE_URL is not set, with a clear reason.
"""

from __future__ import annotations

import os
from typing import Any, cast

import httpx
import pytest

from general_ludd.infra.utilization import UtilizationTracker
from general_ludd.models.gateway import ModelGateway, ModelProfile
from general_ludd.models.provider_registry import ProviderRegistry
from general_ludd.secrets.env import EnvSecretsManager


def _get_env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


def require_backend() -> str:
    """Return AZURE_BASE_URL or pytest.skip with a clear reason.

    Also does a quick HTTP HEAD to verify reachability.
    """
    base_url = _get_env("AZURE_BASE_URL")
    if not base_url:
        pytest.skip("AZURE_BASE_URL not set — export it to run Azure E2E")
    try:
        r = httpx.head(base_url, timeout=10.0)
        r.raise_for_status()
    except Exception as exc:
        pytest.skip(f"AZURE_BASE_URL ({base_url}) unreachable: {exc}")
    return base_url


def _get_api_key() -> str | None:
    return os.environ.get("AZURE_API_KEY") or os.environ.get("AZURE_OPENAI_API_KEY")


def _get_model() -> str:
    return _get_env("AZURE_MODEL") or "gpt-4o"


def _backend_kind() -> str:
    return _get_env("AZURE_BACKEND_KIND") or "azure_openai"


def build_azure_profile() -> ModelProfile:
    return ModelProfile(
        model_profile_id="azure_e2e",
        provider="openai",
        provider_package="langchain_openai",
        provider_class_hint="ChatOpenAI",
        model_name=_get_model(),
        api_base_alias="AZURE_BASE_URL",
        credential_alias="AZURE_API_KEY",
        context_window=128000,
        max_input_tokens=120000,
        max_output_tokens=8000,
        cost_per_input_token=0.000003,
        cost_per_output_token=0.000015,
        api_metered=True,
        run_budget_usd=200.0,
        enabled=True,
        resource_profile="ai_heavy",
        roles=["coder", "planner", "reviewer"],
        latency_class="fast",
        quality_class="high",
    )


class SpyBudgetGuard:
    """Records ``record_spend`` calls for assertion."""

    def __init__(self) -> None:
        self.spend_records: list[float] = []

    def record_spend(self, cost: float) -> None:
        self.spend_records.append(cost)


def build_azure_gateway() -> tuple[ModelGateway, SpyBudgetGuard]:
    profile = build_azure_profile()

    registry = ProviderRegistry()
    registry.register_provider("openai", "langchain_openai", "ChatOpenAI")

    secrets = EnvSecretsManager()
    secrets.set("AZURE_BASE_URL", require_backend())
    api_key = _get_api_key()
    if api_key:
        secrets.set("AZURE_API_KEY", api_key)

    guard = SpyBudgetGuard()

    gateway = ModelGateway(
        profiles=[profile],
        provider_registry=registry,
        secrets_manager=cast(Any, secrets),
        budget_guard=guard,
    )
    return gateway, guard


@pytest.mark.e2e
class TestAzureEnvPointer:
    def test_azure_backend_model_call_and_bill(self) -> None:
        gateway, guard = build_azure_gateway()

        response = gateway.call_model(
            "azure_e2e",
            messages=[{"role": "user", "content": "Reply with exactly: pong"}],
            estimated_cost=0.0,
            budget_remaining=10.0,
        )

        assert "pong" in response.content.lower(), f"Expected 'pong' in response, got: {response.content}"
        assert response.cost_estimate > 0, f"Expected positive cost_estimate, got: {response.cost_estimate}"
        assert len(guard.spend_records) >= 1, "Expected budget guard to record at least one spend"
        assert all(c > 0 for c in guard.spend_records), (
            f"All spend records must be positive, got: {guard.spend_records}"
        )

    def test_azure_backend_model_discovery(self) -> None:
        base_url = require_backend()
        kind = _backend_kind()

        if kind == "azure_openai":
            pytest.skip(
                f"AZURE_BACKEND_KIND={kind} — Azure OpenAI uses a different listing API; skipping /v1/models discovery"
            )

        model = _get_model()
        with httpx.Client(base_url=base_url.rstrip("/"), timeout=30.0) as c:
            r = c.get("/v1/models")
            assert r.status_code == 200, f"GET /v1/models returned {r.status_code}: {r.text}"
            data = r.json()
            objs = data.get("data", [])
            model_ids = {m.get("id", "") for m in objs}
            assert model in model_ids, f"AZURE_MODEL={model!r} not found in /v1/models: {sorted(model_ids)}"

    def test_azure_endpoint_registers_in_tracker(self) -> None:
        base_url = require_backend()
        model = _get_model()

        tracker = UtilizationTracker()
        ep = tracker.register_endpoint(
            endpoint_id="azure_env_pointer",
            url=base_url,
            model=model,
        )

        report = tracker.get_utilization_report()
        endpoint_ids = {e["endpoint_id"] for e in report["endpoints"]}
        assert "azure_env_pointer" in endpoint_ids, f"azure_env_pointer not in report: {report}"
        assert ep.active is True
        assert ep.model == model
