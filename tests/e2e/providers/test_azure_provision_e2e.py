"""E2E tests for Azure full-provision path.

Provisions an Azure GPU VM or Container App, serves vllm/llamacpp, runs model
inference, records billing, and tears down. Opt-in and cost-gated.

Requires: AZURE_PROVISION_E2E=1 + Azure credentials + AZURE_RESOURCE_GROUP.
Gated by GLUDD_E2E_MAX_SPEND_USD (default $5).
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, cast

import httpx
import pytest

from general_ludd.infra.compute import (
    ComputeConfig,
    ComputeInstance,
    ComputeProvider,
    GPUType,
    InferenceEngine,
)
from general_ludd.infra.deployment import DeploymentManager
from general_ludd.secrets.env import EnvSecretsManager


def _get_env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


def _require_azure_creds() -> None:
    if os.environ.get("AZURE_PROVISION_E2E") != "1":
        pytest.skip("AZURE_PROVISION_E2E != '1' — opt-in only")
    missing = []
    for var in (
        "ARM_SUBSCRIPTION_ID",
        "ARM_TENANT_ID",
        "ARM_CLIENT_ID",
        "ARM_CLIENT_SECRET",
        "AZURE_SUBSCRIPTION_ID",
    ):
        if not _get_env(var):
            missing.append(var)
    if missing:
        pytest.skip(f"Azure credentials not set (missing: {', '.join(missing)})")


def _resolve_gpu() -> GPUType:
    raw = _get_env("AZURE_GPU_TYPE", "a100_80").lower()
    known = {g.value: g for g in GPUType}
    if raw in known:
        return known[raw]
    pytest.skip(f"AZURE_GPU_TYPE={raw!r} — unknown GPU type (known: {sorted(known)})")


def _resolve_engine() -> InferenceEngine:
    raw = _get_env("AZURE_PROVISION_ENGINE", "vllm").lower()
    if raw == "vllm":
        return InferenceEngine.VLLM
    if raw == "llamacpp":
        return InferenceEngine.LLAMACPP
    pytest.skip(f"AZURE_PROVISION_ENGINE={raw!r} — unsupported (use vllm or llamacpp)")


def _run_async(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _wait_endpoint(url: str, timeout: float = 600.0) -> None:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        try:
            r = httpx.get(f"{url}/v1/models", timeout=10.0)
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(10)
    pytest.fail(f"Endpoint {url} not reachable after {timeout}s")


def _run_inference(url: str, model: str) -> None:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly: pong"}],
        "max_tokens": 32,
    }
    r = httpx.post(f"{url}/v1/chat/completions", json=payload, timeout=60.0)
    assert r.status_code == 200, f"Chat completions returned {r.status_code}: {r.text}"
    data = r.json()
    content = data["choices"][0]["message"]["content"]
    assert "pong" in content.lower(), f"Expected 'pong' in response, got: {content}"


@pytest.mark.azure_provision
class TestAzureProvisionE2E:
    def test_azure_provision_model_call_and_destroy(self) -> None:
        _require_azure_creds()

        max_spend = max(float(_get_env("GLUDD_E2E_MAX_SPEND_USD", "5")), 1.0)
        engine = _resolve_engine()
        gpu = _resolve_gpu()
        deploy_type = _get_env("AZURE_DEPLOY_TYPE", "vm") or "vm"
        model = _get_env("AZURE_PROVISION_MODEL", "microsoft/phi-2")

        config = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=gpu,
            engine=engine,
            model_name=model,
            deploy_type=deploy_type,
            max_cost_usd=max_spend,
            timeout_minutes=15.0,
            disk_size_gb=100,
        )

        secrets = cast(Any, EnvSecretsManager())
        mgr = DeploymentManager(secrets_resolver=secrets)
        instance: ComputeInstance | None = None

        try:
            instance = _run_async(mgr.deploy(config))
            assert instance is not None
            assert instance.status == "running", f"Expected 'running', got {instance.status!r}"
            assert instance.endpoint_url, "endpoint_url must be set after deploy"

            _wait_endpoint(instance.endpoint_url)
            _run_inference(instance.endpoint_url, model)
            assert instance.cost_incurred > 0, "Expected cost_incurred > 0 after inference"

        finally:
            if instance is not None:
                _run_async(mgr.destroy(instance.instance_id))

        assert instance is not None
        assert instance.cost_incurred <= max_spend, (
            f"Cost {instance.cost_incurred:.2f} exceeded max spend {max_spend:.2f}"
        )
