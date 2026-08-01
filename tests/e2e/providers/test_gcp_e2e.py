"""E2E tests for GCP backend — auto-provision + env-pointer.

Three modes, attempted in order:

Mode 1 (env-pointer): GCP_BASE_URL is set → use existing endpoint.
Mode 2 (auto-provision): GCP credentials available, no GCP_BASE_URL →
    provision a GCP GPU instance via DeploymentManager, run tests,
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
from tests.e2e.providers import (
    _get_env,
)

pytestmark = pytest.mark.e2e

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_GCP_ENV_VARS = [
    "GCP_PROJECT_ID",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_CREDENTIALS",
]


def _get_api_key() -> str | None:
    return os.environ.get("GCP_API_KEY") or os.environ.get("GCP_OPENAI_API_KEY")


def _get_model() -> str:
    return _get_env("GCP_MODEL") or "gpt-4o"


def _backend_kind() -> str:
    return _get_env("GCP_BACKEND_KIND") or "gcp_openai"


def _has_gcp_credentials() -> bool:
    return bool(os.environ.get("GCP_PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT"))


# ---------------------------------------------------------------------------
# fixture — gcp endpoint (3-mode)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def gcp_endpoint():
    """Return ``(base_url, provisioned_instance_or_None)``.

    Mode 1 (env-pointer):
        ``GCP_BASE_URL`` is set → ``(url, None)``.
    Mode 2 (auto-provision):
        GCP credentials present, ``GCP_BASE_URL`` absent → provision a
        Compute Engine instance, yield ``(url, ComputeInstance)``, destroy.
    Mode 3 (skip):
        neither → ``pytest.skip`` with reason.
    """
    base_url = _get_env("GCP_BASE_URL")
    if base_url:
        try:
            r = httpx.head(base_url, timeout=10.0)
            r.raise_for_status()
        except Exception as exc:
            pytest.skip(f"GCP_BASE_URL ({base_url}) unreachable: {exc}")
        yield base_url, None
        return

    if not _has_gcp_credentials():
        pytest.skip(
            "No GCP_BASE_URL or GCP credentials (GCP_PROJECT_ID / "
            "GOOGLE_CLOUD_PROJECT) — cannot provision. "
            "Set cloud credentials or pre-provision an endpoint."
        )

    # ── Mode 2: auto-provision ──────────────────────────────────────────
    gpu_name = _get_env("GCP_GPU_TYPE") or "t4"
    model = _get_model()
    engine_name = _get_env("GCP_PROVISION_ENGINE") or "vllm"

    try:
        engine = InferenceEngine(engine_name)
    except ValueError:
        engine = InferenceEngine.VLLM

    gpu_key = gpu_name.lower().replace("-", "_")
    _GPU_MAP: dict[str, GPUType] = {
        "t4": GPUType.T4,
        "a10g": GPUType.A10G,
        "l4": GPUType.L4,
        "a100_80": GPUType.A100_80,
        "h100": GPUType.H100,
    }
    gpu_type = _GPU_MAP.get(gpu_key, GPUType.T4)

    region = _get_env("GCP_REGION") or "us-central1"

    config = ComputeConfig(
        provider=ComputeProvider.GCP,
        gpu_type=gpu_type,
        deploy_type="vm",
        engine=engine,
        model_name=model,
        region=region,
        provider_auth_aliases={v: v for v in _GCP_ENV_VARS},
        max_cost_usd=float(_get_env("GCP_MAX_COST_USD") or "10.0"),
        timeout_minutes=float(_get_env("GCP_TIMEOUT_MINUTES") or "30.0"),
    )

    secrets = EnvSecretsManager()
    secrets.allow_env(*_GCP_ENV_VARS)
    secrets.set("GCP_BASE_URL", "")

    mgr = DeploymentManager(secrets_resolver=secrets)

    print(f"\n=== Provisioning GCP {gpu_name} for {model} ===")
    print(f"    engine={engine_name}  region={region}")

    instance = asyncio.run(mgr.deploy(config))
    endpoint_url = instance.endpoint_url or f"http://{instance.ip_address}:{instance.port}"
    print(f"    endpoint ready: {endpoint_url}")

    yield endpoint_url, instance

    print(f"\n=== Destroying GCP instance {instance.instance_id} ===")
    with contextlib.suppress(Exception):
        asyncio.run(mgr.destroy(instance.instance_id))


# ---------------------------------------------------------------------------
# gateway helpers
# ---------------------------------------------------------------------------


def build_gcp_profile() -> ModelProfile:
    return ModelProfile(
        model_profile_id="gcp_e2e",
        provider="openai",
        provider_package="langchain_openai",
        provider_class_hint="ChatOpenAI",
        model_name=_get_model(),
        api_base_alias="GCP_BASE_URL",
        credential_alias="GCP_API_KEY",
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


def build_gcp_gateway(base_url: str) -> tuple[ModelGateway, SpyBudgetGuard]:
    profile = build_gcp_profile()

    registry = ProviderRegistry()
    registry.register_provider("openai", "langchain_openai", "ChatOpenAI")

    secrets = EnvSecretsManager()
    secrets.set("GCP_BASE_URL", base_url)
    api_key = _get_api_key()
    if api_key:
        secrets.set("GCP_API_KEY", api_key)

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


class TestGCPEnvPointer:
    def test_model_call_and_bill(self, gcp_endpoint) -> None:
        base_url, _instance = gcp_endpoint
        gateway, guard = build_gcp_gateway(base_url)

        response = gateway.call_model(
            "gcp_e2e",
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

    def test_model_discovery(self, gcp_endpoint) -> None:
        base_url, _instance = gcp_endpoint
        kind = _backend_kind()

        if kind == "gcp_openai":
            pytest.skip(
                f"GCP_BACKEND_KIND={kind} — GCP OpenAI uses a different listing API; skipping /v1/models discovery"
            )

        model = _get_model()
        with httpx.Client(base_url=base_url.rstrip("/"), timeout=30.0) as c:
            r = c.get("/v1/models")
            assert r.status_code == 200, f"GET /v1/models returned {r.status_code}: {r.text}"
            data = r.json()
            objs = data.get("data", [])
            model_ids = {m.get("id", "") for m in objs}
            assert model in model_ids, f"GCP_MODEL={model!r} not found in /v1/models: {sorted(model_ids)}"

    def test_endpoint_registers_in_tracker(self, gcp_endpoint) -> None:
        base_url, _instance = gcp_endpoint
        model = _get_model()

        tracker = UtilizationTracker()
        ep = tracker.register_endpoint(
            endpoint_id="gcp_env_pointer",
            url=base_url,
            model=model,
        )

        report = tracker.get_utilization_report()
        endpoint_ids = {e["endpoint_id"] for e in report["endpoints"]}
        assert "gcp_env_pointer" in endpoint_ids, f"gcp_env_pointer not in report: {report}"
        assert ep.active is True
        assert ep.model == model
