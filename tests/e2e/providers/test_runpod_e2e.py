"""E2E tests for RunPod backend — auto-provision + env-pointer.

Three modes, attempted in order:

Mode 1 (env-pointer): RUNPOD_BASE_URL is set → use existing endpoint.
Mode 2 (auto-provision): RUNPOD_API_KEY available, no RUNPOD_BASE_URL →
    provision a RunPod GPU pod via DeploymentManager, run tests,
    destroy in teardown.
Mode 3 (skip): neither endpoint nor API key → skip with clear reason.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from typing import Any, cast

import httpx
import pytest

from general_ludd.infra.compute import (
    ComputeConfig,
    ComputeProvider,
    GPUType,
    InferenceEngine,
)
from general_ludd.infra.deployment import DeploymentManager
from general_ludd.infra.utilization import UtilizationTracker
from general_ludd.models.gateway import ModelGateway, ModelProfile
from general_ludd.models.provider_registry import ProviderRegistry
from general_ludd.secrets.env import EnvSecretsManager
from tests.e2e.providers import (
    _get_env,
)

pytestmark = pytest.mark.e2e

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_RUNPOD_ENV_VARS = [
    "RUNPOD_API_KEY",
]


def _get_api_key() -> str | None:
    return os.environ.get("RUNPOD_API_KEY")


def _get_model() -> str:
    return _get_env("RUNPOD_MODEL") or "gpt-4o"


def _backend_kind() -> str:
    return _get_env("RUNPOD_BACKEND_KIND") or "runpod_openai"


def _has_runpod_credentials() -> bool:
    return bool(os.environ.get("RUNPOD_API_KEY"))


# ---------------------------------------------------------------------------
# fixture — runpod endpoint (3-mode)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def runpod_endpoint():
    """Return ``(base_url, provisioned_instance_or_None)``.

    Mode 1 (env-pointer):
        ``RUNPOD_BASE_URL`` is set → ``(url, None)``.
    Mode 2 (auto-provision):
        ``RUNPOD_API_KEY`` present, ``RUNPOD_BASE_URL`` absent → provision a
        RunPod GPU pod, yield ``(url, ComputeInstance)``, destroy in teardown.
    Mode 3 (skip):
        neither → ``pytest.skip`` with reason.
    """
    base_url = _get_env("RUNPOD_BASE_URL")
    if base_url:
        try:
            r = httpx.head(base_url, timeout=10.0)
            r.raise_for_status()
        except Exception as exc:
            pytest.skip(f"RUNPOD_BASE_URL ({base_url}) unreachable: {exc}")
        yield base_url, None
        return

    if not _has_runpod_credentials():
        pytest.skip(
            "No RUNPOD_BASE_URL or RUNPOD_API_KEY — cannot provision. "
            "Set RUNPOD_API_KEY or pre-provision a RunPod endpoint."
        )

    # ── Mode 2: auto-provision ──────────────────────────────────────────
    gpu_name = _get_env("RUNPOD_GPU_TYPE") or "a100_80"
    model = _get_model()
    engine_name = _get_env("RUNPOD_PROVISION_ENGINE") or "vllm"

    try:
        engine = InferenceEngine(engine_name)
    except ValueError:
        engine = InferenceEngine.VLLM

    gpu_key = gpu_name.lower().replace("-", "_")
    _GPU_MAP: dict[str, GPUType] = {
        "t4": GPUType.T4,
        "a10g": GPUType.A10G,
        "a40": GPUType.A40,
        "a100_80": GPUType.A100_80,
        "h100": GPUType.H100,
    }
    gpu_type = _GPU_MAP.get(gpu_key, GPUType.A100_80)

    config = ComputeConfig(
        provider=ComputeProvider.RUNPOD,
        gpu_type=gpu_type,
        deploy_type="runpod",
        engine=engine,
        model_name=model,
        region="us",
        provider_auth_aliases={v: v for v in _RUNPOD_ENV_VARS},
        max_cost_usd=float(_get_env("RUNPOD_MAX_COST_USD") or "10.0"),
        timeout_minutes=float(_get_env("RUNPOD_TIMEOUT_MINUTES") or "30.0"),
    )

    secrets = EnvSecretsManager()
    secrets.allow_env(*_RUNPOD_ENV_VARS)
    secrets.set("RUNPOD_BASE_URL", "")

    mgr = DeploymentManager(secrets_resolver=secrets)

    print(f"\n=== Provisioning RunPod {gpu_name} for {model} ===")
    print(f"    engine={engine_name}")

    instance = asyncio.run(mgr.deploy(config))
    endpoint_url = instance.endpoint_url or f"http://{instance.ip_address}:{instance.port}"
    print(f"    endpoint ready: {endpoint_url}")

    yield endpoint_url, instance

    print(f"\n=== Destroying RunPod instance {instance.instance_id} ===")
    with contextlib.suppress(Exception):
        asyncio.run(mgr.destroy(instance.instance_id))


# ---------------------------------------------------------------------------
# gateway helpers
# ---------------------------------------------------------------------------


def build_runpod_profile() -> ModelProfile:
    return ModelProfile(
        model_profile_id="runpod_e2e",
        provider="openai",
        provider_package="langchain_openai",
        provider_class_hint="ChatOpenAI",
        model_name=_get_model(),
        api_base_alias="RUNPOD_BASE_URL",
        credential_alias="RUNPOD_API_KEY",
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
    def __init__(self) -> None:
        self.spend_records: list[float] = []

    def record_spend(self, cost: float) -> None:
        self.spend_records.append(cost)


def build_runpod_gateway(base_url: str) -> tuple[ModelGateway, SpyBudgetGuard]:
    profile = build_runpod_profile()

    registry = ProviderRegistry()
    registry.register_provider("openai", "langchain_openai", "ChatOpenAI")

    secrets = EnvSecretsManager()
    secrets.set("RUNPOD_BASE_URL", base_url)
    api_key = _get_api_key()
    if api_key:
        secrets.set("RUNPOD_API_KEY", api_key)

    guard = SpyBudgetGuard()

    gateway = ModelGateway(
        profiles=[profile],
        provider_registry=registry,
        secrets_manager=cast(Any, secrets),
        budget_guard=guard,
    )
    return gateway, guard


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


class TestRunPodEnvPointer:
    def test_model_call_and_bill(self, runpod_endpoint) -> None:
        base_url, _instance = runpod_endpoint
        gateway, guard = build_runpod_gateway(base_url)

        response = gateway.call_model(
            "runpod_e2e",
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

    def test_model_discovery(self, runpod_endpoint) -> None:
        base_url, _instance = runpod_endpoint
        kind = _backend_kind()

        if kind == "runpod_openai":
            pytest.skip(
                f"RUNPOD_BACKEND_KIND={kind} — RunPod OpenAI uses a "
                "different listing API; skipping /v1/models discovery"
            )

        model = _get_model()
        with httpx.Client(base_url=base_url.rstrip("/"), timeout=30.0) as c:
            r = c.get("/v1/models")
            assert r.status_code == 200, f"GET /v1/models returned {r.status_code}: {r.text}"
            data = r.json()
            objs = data.get("data", [])
            model_ids = {m.get("id", "") for m in objs}
            assert model in model_ids, f"RUNPOD_MODEL={model!r} not found in /v1/models: {sorted(model_ids)}"

    def test_endpoint_registers_in_tracker(self, runpod_endpoint) -> None:
        base_url, _instance = runpod_endpoint
        model = _get_model()

        tracker = UtilizationTracker()
        ep = tracker.register_endpoint(
            endpoint_id="runpod_env_pointer",
            url=base_url,
            model=model,
        )

        report = tracker.get_utilization_report()
        endpoint_ids = {e["endpoint_id"] for e in report["endpoints"]}
        assert "runpod_env_pointer" in endpoint_ids, f"runpod_env_pointer not in report: {report}"
        assert ep.active is True
        assert ep.model == model
