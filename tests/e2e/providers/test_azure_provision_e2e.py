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

from general_ludd.events import EventBus
from general_ludd.infra.compute import (
    ComputeConfig,
    ComputeInstance,
    ComputeProvider,
    GPUType,
    InferenceEngine,
)
from general_ludd.infra.deploy_strategy import (
    DeployStrategist,
    DeployUrgency,
    ResourceTier,
)
from general_ludd.infra.deployment import DeploymentManager
from general_ludd.secrets.env import EnvSecretsManager


def _get_env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


def _require_azure_creds() -> None:
    if os.environ.get("AZURE_PROVISION_E2E") != "1":
        pytest.skip("AZURE_PROVISION_E2E != '1' — opt-in only")

    msi = _get_env("ARM_USE_MSI") == "true"
    sub_id = _get_env("ARM_SUBSCRIPTION_ID") or _get_env("AZURE_SUBSCRIPTION_ID")

    if sub_id and (msi or (_get_env("ARM_TENANT_ID") and _get_env("ARM_CLIENT_ID"))):
        return  # managed identity or service principal credentials present

    pytest.skip(
        "Azure credentials not set. Source your env file first:\n"
        "  source /tmp/general-ludd.env\n"
        "Or set ARM_SUBSCRIPTION_ID + ARM_TENANT_ID + ARM_CLIENT_ID (+ ARM_CLIENT_SECRET).\n"
        "Or for managed identity: ARM_USE_MSI=true + ARM_SUBSCRIPTION_ID."
    )


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
@pytest.mark.timeout(2400)
class TestAzureProvisionE2E:
    def test_azure_provision_model_call_and_destroy(self) -> None:
        _require_azure_creds()

        max_spend = max(float(_get_env("GLUDD_E2E_MAX_SPEND_USD", "5")), 1.0)
        engine = _resolve_engine()
        gpu = _resolve_gpu()
        deploy_type = _get_env("AZURE_DEPLOY_TYPE", "containerapp") or "containerapp"
        model = _get_env("AZURE_MODEL", "Qwen/Qwen2.5-0.5B-Instruct")

        config = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=gpu,
            engine=engine,
            model_name=model,
            region=_get_env("AZURE_REGION") or "eastus",
            deploy_type=deploy_type,
            max_cost_usd=max_spend,
            timeout_minutes=15.0,
            disk_size_gb=100,
        )

        strategist = DeployStrategist()
        urgency = (
            DeployUrgency.IMMEDIATE
            if _get_env("AZURE_URGENCY", "normal").lower() == "immediate"
            else DeployUrgency.NORMAL
        )
        plan = strategist.plan(urgency, config.gpu_type.value, config.model_name)
        warmup_id = plan.warmup.tier_id if plan.warmup else "none"
        print(
            f"[test] Deploy plan: primary={plan.primary.tier_id}, "
            f"warmup={warmup_id}, cost=${plan.estimated_cost_usd:.4f}",
            flush=True,
        )

        secrets = cast(Any, EnvSecretsManager())
        terraform_events: list[str] = []
        event_bus = EventBus()

        def report_terraform_event(event: Any) -> None:
            name = str(event.payload.get("name", ""))
            terraform_events.append(name)
            if name != "terraform_output":
                operation = event.payload.get("operation", "")
                print(f"[event] {name} {operation}".rstrip(), flush=True)

        event_bus.subscribe("custom", report_terraform_event)
        mgr = DeploymentManager(secrets_resolver=secrets, event_bus=event_bus)
        instance: ComputeInstance | None = None

        try:
            print(f"[test] Deploying {gpu} GPU in {deploy_type} mode...", flush=True)
            instance = _run_async(mgr.deploy(config))
            assert instance is not None
            assert instance.status == "running", f"Expected 'running', got {instance.status!r}"
            assert instance.endpoint_url, "endpoint_url must be set after deploy"
            assert terraform_events[0] == "terraform_deploy_started"
            assert "terraform_output" in terraform_events
            assert "terraform_deploy_completed" in terraform_events
            print(f"[test] Deploy done, endpoint: {instance.endpoint_url}", flush=True)

            print(f"[test] Waiting for endpoint {instance.endpoint_url}...", flush=True)
            start = time.monotonic()
            max_attempts = 60
            for i in range(max_attempts):
                try:
                    r = httpx.get(f"{instance.endpoint_url}/v1/models", timeout=10.0)
                    if r.status_code == 200:
                        break
                except Exception:
                    pass
                print(f"[test] Polling endpoint... ({i + 1}/{max_attempts})", flush=True)
                time.sleep(10)
            else:
                pytest.fail(f"Endpoint {instance.endpoint_url} not reachable after {max_attempts * 10}s")
            elapsed = time.monotonic() - start
            print(f"[test] Endpoint ready after {elapsed:.0f}s", flush=True)

            _run_inference(instance.endpoint_url, model)
            print("[test] Inference response received", flush=True)
            assert instance.cost_incurred > 0, "Expected cost_incurred > 0 after inference"

            tier = ResourceTier.CONTAINER_APP if deploy_type == "containerapp" else ResourceTier.DEDICATED_VM
            strategist.learn_from_history(tier, instance.cost_incurred, 600)
            print(f"[test] Cost history entries: {len(strategist.cost_history)}", flush=True)
            assert len(strategist.cost_history) == 1

        finally:
            if instance is not None:
                print(f"[test] Destroying {instance.instance_id}...", flush=True)
                _run_async(mgr.destroy(instance.instance_id))
                print("[test] Destroy complete", flush=True)

        assert instance is not None
        assert "terraform_destroy_started" in terraform_events
        assert "terraform_destroy_completed" in terraform_events
        assert instance.cost_incurred <= max_spend, (
            f"Cost {instance.cost_incurred:.2f} exceeded max spend {max_spend:.2f}"
        )
