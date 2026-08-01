"""E2E tests for AWS backend — auto-provision + env-pointer.

Three modes, attempted in order:

Mode 1 (env-pointer): AWS_BASE_URL is set → use existing endpoint.
Mode 2 (auto-provision): AWS credentials available, no AWS_BASE_URL →
    provision an AWS GPU instance via DeploymentManager, run tests,
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

_AWS_ENV_VARS = [
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
]


def _get_api_key() -> str | None:
    return os.environ.get("AWS_API_KEY") or os.environ.get("AWS_OPENAI_API_KEY")


def _get_model() -> str:
    return _get_env("AWS_MODEL") or "gpt-4o"


def _backend_kind() -> str:
    return _get_env("AWS_BACKEND_KIND") or "aws_openai"


def _has_aws_credentials() -> bool:
    return bool(os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"))


# ---------------------------------------------------------------------------
# fixture — aws endpoint (3-mode)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def aws_endpoint():
    """Return ``(base_url, provisioned_instance_or_None)``.

    Mode 1 (env-pointer):
        ``AWS_BASE_URL`` is set → ``(url, None)``.
    Mode 2 (auto-provision):
        AWS credentials present, ``AWS_BASE_URL`` absent → provision an
        EC2 instance, yield ``(url, ComputeInstance)``, destroy in teardown.
    Mode 3 (skip):
        neither → ``pytest.skip`` with reason.
    """
    base_url = _get_env("AWS_BASE_URL")
    if base_url:
        try:
            r = httpx.head(base_url, timeout=10.0)
            r.raise_for_status()
        except Exception as exc:
            pytest.skip(f"AWS_BASE_URL ({base_url}) unreachable: {exc}")
        yield base_url, None
        return

    if not _has_aws_credentials():
        pytest.skip(
            "No AWS_BASE_URL or AWS credentials (AWS_ACCESS_KEY_ID + "
            "AWS_SECRET_ACCESS_KEY) — cannot provision. "
            "Set cloud credentials or pre-provision an endpoint."
        )

    # ── Mode 2: auto-provision ──────────────────────────────────────────
    gpu_name = _get_env("AWS_GPU_TYPE") or "a10g"
    model = _get_model()
    engine_name = _get_env("AWS_PROVISION_ENGINE") or "vllm"

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
    gpu_type = _GPU_MAP.get(gpu_key, GPUType.A10G)

    region = _get_env("AWS_REGION") or "us-east-1"

    config = ComputeConfig(
        provider=ComputeProvider.AWS,
        gpu_type=gpu_type,
        deploy_type="vm",
        engine=engine,
        model_name=model,
        region=region,
        provider_auth_aliases={v: v for v in _AWS_ENV_VARS},
        max_cost_usd=float(_get_env("AWS_MAX_COST_USD") or "10.0"),
        timeout_minutes=float(_get_env("AWS_TIMEOUT_MINUTES") or "30.0"),
    )

    secrets = EnvSecretsManager()
    secrets.allow_env(*_AWS_ENV_VARS, "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
    secrets.set("AWS_BASE_URL", "")

    mgr = DeploymentManager(secrets_resolver=secrets)

    print(f"\n=== Provisioning AWS {gpu_name} for {model} ===")
    print(f"    engine={engine_name}  region={region}")

    instance = asyncio.run(mgr.deploy(config))
    endpoint_url = instance.endpoint_url or f"http://{instance.ip_address}:{instance.port}"
    print(f"    endpoint ready: {endpoint_url}")

    yield endpoint_url, instance

    print(f"\n=== Destroying AWS instance {instance.instance_id} ===")
    with contextlib.suppress(Exception):
        asyncio.run(mgr.destroy(instance.instance_id))


# ---------------------------------------------------------------------------
# gateway helpers
# ---------------------------------------------------------------------------


def build_aws_profile() -> ModelProfile:
    return ModelProfile(
        model_profile_id="aws_e2e",
        provider="openai",
        provider_package="langchain_openai",
        provider_class_hint="ChatOpenAI",
        model_name=_get_model(),
        api_base_alias="AWS_BASE_URL",
        credential_alias="AWS_API_KEY",
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


def build_aws_gateway(base_url: str) -> tuple[ModelGateway, SpyBudgetGuard]:
    profile = build_aws_profile()

    registry = ProviderRegistry()
    registry.register_provider("openai", "langchain_openai", "ChatOpenAI")

    secrets = EnvSecretsManager()
    secrets.set("AWS_BASE_URL", base_url)
    api_key = _get_api_key()
    if api_key:
        secrets.set("AWS_API_KEY", api_key)

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


class TestAWSEnvPointer:
    def test_model_call_and_bill(self, aws_endpoint) -> None:
        base_url, _instance = aws_endpoint
        gateway, guard = build_aws_gateway(base_url)

        response = gateway.call_model(
            "aws_e2e",
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

    def test_model_discovery(self, aws_endpoint) -> None:
        base_url, _instance = aws_endpoint
        kind = _backend_kind()

        if kind == "aws_openai":
            pytest.skip(
                f"AWS_BACKEND_KIND={kind} — AWS OpenAI uses a different listing API; skipping /v1/models discovery"
            )

        model = _get_model()
        with httpx.Client(base_url=base_url.rstrip("/"), timeout=30.0) as c:
            r = c.get("/v1/models")
            assert r.status_code == 200, f"GET /v1/models returned {r.status_code}: {r.text}"
            data = r.json()
            objs = data.get("data", [])
            model_ids = {m.get("id", "") for m in objs}
            assert model in model_ids, f"AWS_MODEL={model!r} not found in /v1/models: {sorted(model_ids)}"

    def test_endpoint_registers_in_tracker(self, aws_endpoint) -> None:
        base_url, _instance = aws_endpoint
        model = _get_model()

        tracker = UtilizationTracker()
        ep = tracker.register_endpoint(
            endpoint_id="aws_env_pointer",
            url=base_url,
            model=model,
        )

        report = tracker.get_utilization_report()
        endpoint_ids = {e["endpoint_id"] for e in report["endpoints"]}
        assert "aws_env_pointer" in endpoint_ids, f"aws_env_pointer not in report: {report}"
        assert ep.active is True
        assert ep.model == model
