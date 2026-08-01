"""Shared fixtures and helpers for cloud-provider E2E tests.

Three-mode pattern (per-provider):
  Mode 1 (env-pointer): ``{PROVIDER}_BASE_URL`` is set -> use existing endpoint.
  Mode 2 (auto-provision): cloud credentials present, no endpoint ->
      provision via DeploymentManager, test, destroy in teardown.
  Mode 3 (skip): neither endpoint nor credentials -> skip with clear reason.
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
    ComputeProvider,
    GPUType,
    InferenceEngine,
)
from general_ludd.infra.deployment import DeploymentManager
from general_ludd.secrets.env import EnvSecretsManager


def _get_env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


_PROVIDER_ENV_MAP: dict[str, list[str]] = {
    "aws": [
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
    ],
    "gcp": [
        "GCP_PROJECT_ID",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CREDENTIALS",
    ],
    "runpod": [
        "RUNPOD_API_KEY",
    ],
}

_PROVIDER_GPU_DEFAULT: dict[str, str] = {
    "aws": "a10g",
    "gcp": "t4",
    "runpod": "a100_80",
}


def require_cloud_creds(provider: str) -> bool:
    env_vars = _PROVIDER_ENV_MAP.get(provider, [])
    has_any = any(os.environ.get(k) for k in env_vars)
    if has_any:
        return True
    env_names = " , ".join(env_vars[:3])
    more = f" (+{len(env_vars) - 3} more)" if len(env_vars) > 3 else ""
    pytest.skip(
        f"No {provider.upper()} credentials ({env_names}{more}) "
        f"— cannot provision. Set cloud credentials or "
        f"pre-provision an endpoint and set {provider.upper()}_BASE_URL."
    )


def build_deployment_manager(
    provider: str,
    env_var_names: list[str],
    model: str = "",
    gpu_type: GPUType | None = None,
    region: str | None = None,
    engine: InferenceEngine = InferenceEngine.VLLM,
    max_cost_usd: float = 10.0,
    timeout_minutes: float = 30.0,
    deploy_type: str = "vm",
) -> tuple[DeploymentManager, ComputeConfig]:
    provider_enum = ComputeProvider(provider)
    gpu = gpu_type or GPUType(_PROVIDER_GPU_DEFAULT.get(provider, "t4"))
    config = ComputeConfig(
        provider=provider_enum,
        gpu_type=gpu,
        deploy_type=deploy_type,
        engine=engine,
        model_name=model,
        region=region or "",
        provider_auth_aliases={v: v for v in env_var_names},
        max_cost_usd=max_cost_usd,
        timeout_minutes=timeout_minutes,
    )
    secrets = EnvSecretsManager()
    secrets.allow_env(*env_var_names)
    mgr = DeploymentManager(secrets_resolver=secrets)
    return mgr, config


async def _wait_endpoint_async(url: str, timeout: float = 300.0) -> str:
    deadline = time.monotonic() + timeout
    last_error: str | None = None
    while time.monotonic() < deadline:
        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.head(url)
                if r.status_code == 200:
                    return url
                last_error = f"HTTP {r.status_code}"
        except Exception as exc:
            last_error = str(exc)
        await asyncio.sleep(5.0)
    raise TimeoutError(f"Endpoint {url} not ready after {timeout:.0f}s: {last_error}")


def wait_endpoint(url: str, timeout: float = 300.0) -> str:
    return asyncio.run(_wait_endpoint_async(url, timeout))


async def _run_inference_async(endpoint_url: str, prompt: str = "Reply with exactly: pong") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": "default",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 16,
        "temperature": 0.0,
    }
    async with httpx.AsyncClient(timeout=60.0) as c:
        r = await c.post(
            f"{endpoint_url.rstrip('/')}/v1/chat/completions",
            json=payload,
        )
        r.raise_for_status()
        response = r.json()
        if not isinstance(response, dict):
            raise ValueError("Inference response must be a JSON object")
        return cast(dict[str, Any], response)


def run_inference(endpoint_url: str, prompt: str = "Reply with exactly: pong") -> dict[str, Any]:
    return asyncio.run(_run_inference_async(endpoint_url, prompt))


async def _list_models_async(endpoint_url: str) -> list[str]:
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.get(f"{endpoint_url.rstrip('/')}/v1/models")
        r.raise_for_status()
        data = r.json()
        return [m.get("id", "") for m in data.get("data", [])]


def list_models(endpoint_url: str) -> list[str]:
    return asyncio.run(_list_models_async(endpoint_url))
