"""E2E tests for Azure backend — auto-provision + env-pointer.

Three modes, attempted in order:

Mode 1 (env-pointer): AZURE_BASE_URL is set → use existing endpoint.
Mode 2 (auto-provision): ARM credentials available, no AZURE_BASE_URL →
    provision an Azure GPU ContainerApp via DeploymentManager, run tests,
    destroy in teardown.
Mode 3 (skip): neither endpoint nor credentials → skip with clear reason.
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

pytestmark = pytest.mark.e2e

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_ARM_ENV_VARS = [
    "ARM_SUBSCRIPTION_ID",
    "ARM_TENANT_ID",
    "ARM_CLIENT_ID",
    "ARM_CLIENT_SECRET",
    "ARM_USE_MSI",
]

_GPU_TYPE_MAP: dict[str, GPUType] = {
    "t4": GPUType.T4,
    "a10g": GPUType.A10G,
    "l4": GPUType.L4,
    "a10": GPUType.A10,
    "rtx_4090": GPUType.RTX_4090,
    "rtx_6000_ada": GPUType.RTX_6000_ADA,
    "a40": GPUType.A40,
    "l40s": GPUType.L40S,
    "amd_mi250": GPUType.AMD_MI250,
    "a100_40": GPUType.A100_40,
    "a100_80": GPUType.A100_80,
    "h100": GPUType.H100,
    "h200": GPUType.H200,
}


def _get_env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


def _get_api_key() -> str | None:
    return os.environ.get("AZURE_API_KEY") or os.environ.get("AZURE_OPENAI_API_KEY")


def _get_model() -> str:
    return _get_env("AZURE_MODEL") or "gpt-4o"


def _backend_kind() -> str:
    return _get_env("AZURE_BACKEND_KIND") or "azure_openai"


def _has_arm_credentials() -> bool:
    return any(os.environ.get(k) for k in _ARM_ENV_VARS) or bool(os.environ.get("AZURE_SUBSCRIPTION_ID"))


def _resolve_gpu_type(name: str) -> GPUType:
    key = name.lower().replace("-", "_")
    if key in _GPU_TYPE_MAP:
        return _GPU_TYPE_MAP[key]
    try:
        return GPUType(key)
    except ValueError:
        return GPUType.A100_80


# ---------------------------------------------------------------------------
# fixture — azure endpoint (3-mode)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def azure_endpoint():
    """Return ``(base_url, provisioned_instance_or_None)``.

    Mode 1 (env-pointer):
        ``AZURE_BASE_URL`` is set → ``(url, None)``.
    Mode 2 (auto-provision):
        ARM credentials present, ``AZURE_BASE_URL`` absent → provision a
        ContainerApp, yield ``(url, ComputeInstance)``, destroy in teardown.
    Mode 3 (skip):
        neither → ``pytest.skip`` with reason.
    """
    base_url = _get_env("AZURE_BASE_URL")
    if base_url:
        try:
            r = httpx.head(base_url, timeout=10.0)
            r.raise_for_status()
        except Exception as exc:
            pytest.skip(f"AZURE_BASE_URL ({base_url}) unreachable: {exc}")
        yield base_url, None
        return

    if not _has_arm_credentials():
        pytest.skip(
            "No AZURE_BASE_URL or ARM credentials — cannot provision. "
            "Source your env file: source /tmp/general-ludd.env"
        )

    # ── Mode 2: auto-provision ──────────────────────────────────────────
    gpu_name = _get_env("AZURE_GPU_TYPE") or "a100_80"
    model = _get_model()
    engine_name = _get_env("AZURE_PROVISION_ENGINE") or "vllm"

    try:
        engine = InferenceEngine(engine_name)
    except ValueError:
        engine = InferenceEngine.VLLM

    config = ComputeConfig(
        provider=ComputeProvider.AZURE,
        gpu_type=_resolve_gpu_type(gpu_name),
        deploy_type="containerapp",
        engine=engine,
        model_name=model,
        region=_get_env("AZURE_REGION") or "eastus",
        provider_auth_aliases={v: v for v in _ARM_ENV_VARS},
        max_cost_usd=float(_get_env("AZURE_MAX_COST_USD") or "10.0"),
        timeout_minutes=float(_get_env("AZURE_TIMEOUT_MINUTES") or "30.0"),
    )

    secrets = EnvSecretsManager()
    secrets.allow_env(*_ARM_ENV_VARS, "AZURE_SUBSCRIPTION_ID")
    secrets.set("AZURE_BASE_URL", "")

    mgr = DeploymentManager(secrets_resolver=secrets)

    print(f"\n=== Provisioning Azure {gpu_name} for {model} ===")
    print(f"    engine={engine_name}  region={config.region}")

    instance = asyncio.run(mgr.deploy(config))
    endpoint_url = instance.endpoint_url or f"http://{instance.ip_address}:{instance.port}"
    print(f"    endpoint ready: {endpoint_url}")

    yield endpoint_url, instance

    print(f"\n=== Destroying Azure instance {instance.instance_id} ===")
    with contextlib.suppress(Exception):
        asyncio.run(mgr.destroy(instance.instance_id))


# ---------------------------------------------------------------------------
# gateway helpers (unchanged logic, parameterised on base_url)
# ---------------------------------------------------------------------------


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


def build_azure_gateway(base_url: str) -> tuple[ModelGateway, SpyBudgetGuard]:
    profile = build_azure_profile()

    registry = ProviderRegistry()
    registry.register_provider("openai", "langchain_openai", "ChatOpenAI")

    secrets = EnvSecretsManager()
    secrets.set("AZURE_BASE_URL", base_url)
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


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


class TestAzureEnvPointer:
    def test_model_call_and_bill(self, azure_endpoint) -> None:
        base_url, _instance = azure_endpoint
        gateway, guard = build_azure_gateway(base_url)

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

    def test_model_discovery(self, azure_endpoint) -> None:
        base_url, _instance = azure_endpoint
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

    def test_endpoint_registers_in_tracker(self, azure_endpoint) -> None:
        base_url, _instance = azure_endpoint
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
