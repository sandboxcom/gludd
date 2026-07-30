"""Low-cost provider smoke-test registry and runner.

The smoke runner is intentionally conservative: default runs validate local
configuration and emit a complete evidence bundle without provisioning cloud
resources or making model calls. Operators opt into live HTTP probes with the
CLI flag, and those probes are limited to cheap metadata endpoints.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from general_ludd.infra.compute import (
    ComputeConfig,
    ComputeInstance,
    ComputeProvider,
    GPUType,
    InferenceEngine,
)
from general_ludd.infra.deployment import DeploymentManager
from general_ludd.infra.providers import ProviderInfo
from general_ludd.infra.providers import ProviderRegistry as ComputeProviderRegistry
from general_ludd.models.provider_presets import (
    PROVIDER_FLAGSHIP_MODELS,
    PROVIDER_PRESETS,
)

HttpGet = Callable[[str, float], Any]
HttpPost = Callable[[str, dict[str, str], dict[str, object], float], Any]
DEFAULT_PROVISIONED_MODEL = "TinyLlama/TinyLlama-1.1B-Chat-v1.0"
MULTI_PROVIDER_CANDIDATES = (
    "openrouter",
    "openai",
    "groq",
    "deepseek",
    "together",
    "fireworks",
    "mistral",
    "cohere",
)
MULTI_PLATFORM_LOCAL_CANDIDATES = ("vllm", "llamacpp", "ollama")
MULTI_PLATFORM_API_CANDIDATES = ("openrouter", "openai", "groq")


class Provisioner(Protocol):
    async def deploy(self, config: ComputeConfig) -> ComputeInstance: ...

    async def destroy(self, instance_id: str) -> None: ...


_COMMON_CONNECTOR_SMOKES: dict[str, tuple[str, ...]] = {
    "github-actions": ("GITHUB_TOKEN",),
    "notion": ("NOTION_TOKEN",),
    "kubernetes": ("KUBECONFIG",),
    "nomad": ("NOMAD_ADDR",),
    "searx": ("SEARXNG_URL",),
    "zendesk": ("ZENDESK_SUBDOMAIN", "ZENDESK_EMAIL", "ZENDESK_API_TOKEN"),
    "clickhouse": ("CLICKHOUSE_URL",),
    "statsd": ("STATSD_HOST", "STATSD_PORT"),
    "windows-wmi": ("WMI_HOST", "WMI_USER", "WMI_PASSWORD"),
    "windows-defender": ("DEFENDER_TENANT_ID", "DEFENDER_CLIENT_ID", "DEFENDER_CLIENT_SECRET"),
}

_LOCAL_BACKEND_SMOKES: dict[str, tuple[str, ...]] = {
    "ollama": ("OLLAMA_BASE_URL",),
    "vllm": ("VLLM_BASE_URL",),
    "llamacpp": ("LLAMACPP_BASE_URL",),
    "slurm": ("SLURM_REST_URL", "SLURM_REST_TOKEN"),
}

_CREDENTIAL_ALIASES: dict[str, tuple[tuple[str, ...], ...]] = {
    "aws": (
        ("AWS_ACCESS_KEY_ID", "AWS_KEY"),
        ("AWS_SECRET_ACCESS_KEY", "AWS_SECRET"),
    ),
    "gcp": (("GOOGLE_APPLICATION_CREDENTIALS", "GCP_CREDENTIALS_JSON", "GOOGLE_CREDENTIALS"),),
    "azure": (("AZURE_SUBSCRIPTION_ID",),),
    "runpod": (("RUNPOD_API_KEY",),),
    "vast": (("VAST_API_KEY", "VAST_AI_API_KEY"),),
    "vast_ai": (("VAST_AI_API_KEY", "VAST_API_KEY"),),
    "qemu": tuple(),
    "kubernetes": (("KUBECONFIG",),),
    "vsphere": (("VSPHERE_USER",), ("VSPHERE_PASSWORD",), ("VSPHERE_SERVER",)),
    "vmware": (("VSPHERE_USER",), ("VSPHERE_PASSWORD",), ("VSPHERE_SERVER",)),
}


@dataclass(frozen=True)
class SmokeSpec:
    provider: str
    test: str
    category: str
    description: str
    required_env: tuple[tuple[str, ...], ...]
    coverage_depth: str = "preflight"
    functional_scope: tuple[str, ...] = ("configuration",)
    estimated_cost_usd: float = 0.0
    default_live: bool = False
    endpoint: str | None = None
    model: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "test": self.test,
            "category": self.category,
            "description": self.description,
            "required_env": ["/".join(group) for group in self.required_env],
            "coverage_depth": self.coverage_depth,
            "functional_scope": list(self.functional_scope),
            "estimated_cost_usd": self.estimated_cost_usd,
            "default_live": self.default_live,
            "endpoint": self.endpoint,
            "model": self.model,
        }


class SmokeRecorder:
    def __init__(
        self,
        provider: str,
        test: str,
        *,
        mode: str,
        estimated_cost_usd: float,
        coverage_depth: str,
        functional_scope: tuple[str, ...],
    ) -> None:
        self.started = time.monotonic()
        self.trace_id = f"trace-{uuid.uuid4().hex}"
        self._sequence = 0
        self.report: dict[str, Any] = {
            "run_id": f"smoke-{uuid.uuid4().hex[:12]}",
            "provider": provider,
            "test": test,
            "trace_id": self.trace_id,
            "coverage_depth": coverage_depth,
            "functional_scope": list(functional_scope),
            "mode": mode,
            "started_at": datetime.now(UTC).isoformat(),
            "completed_at": None,
            "status": "pass",
            "estimated_cost_usd": estimated_cost_usd,
            "logs": [],
            "trace": [],
            "metrics": {
                "checks_total": 0,
                "checks_failed": 0,
                "http_requests": 0,
                "models_seen": 0,
                "duration_ms": 0,
            },
            "events": [],
            "endpoint_diagnostics": {},
            "model_juggle": {},
            "analysis_prompt": _analysis_prompt(provider, test),
        }

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    def log(self, level: str, message: str, **fields: object) -> None:
        entry = {
            "type": "log",
            "ts": datetime.now(UTC).isoformat(),
            "sequence": self._next_sequence(),
            "trace_id": self.trace_id,
            "level": level,
            "message": message,
            "fields": _redact(fields),
        }
        self.report["logs"].append(entry)
        self.report["trace"].append(entry)

    def event(self, name: str, **fields: object) -> None:
        entry = {
            "type": "event",
            "ts": datetime.now(UTC).isoformat(),
            "sequence": self._next_sequence(),
            "trace_id": self.trace_id,
            "name": name,
            "fields": _redact(fields),
        }
        self.report["events"].append(entry)
        self.report["trace"].append(entry)

    def check(self, ok: bool, message: str, **fields: object) -> None:
        self.report["metrics"]["checks_total"] += 1
        if ok:
            self.log("info", message, **fields)
        else:
            self.report["metrics"]["checks_failed"] += 1
            self.report["status"] = "fail"
            self.log("error", message, **fields)

    def finish(self) -> dict[str, Any]:
        self.report["completed_at"] = datetime.now(UTC).isoformat()
        self.report["metrics"]["duration_ms"] = int((time.monotonic() - self.started) * 1000)
        self.event("smoke.completed", status=self.report["status"])
        return self.report


def list_smoke_tests(provider: str | None = None) -> list[dict[str, object]]:
    specs = _build_registry()
    if provider:
        provider_key = _normalize(provider)
        specs = [spec for spec in specs if spec.provider == provider_key]
    return [spec.as_dict() for spec in sorted(specs, key=lambda item: (item.provider, item.test))]


def run_smoke(
    provider: str,
    test: str,
    *,
    env: Mapping[str, str] | None = None,
    live: bool = False,
    timeout: float = 2.0,
    max_cost_usd: float = 0.01,
    base_url: str | None = None,
    model: str | None = None,
    provisioned: bool = False,
    region: str | None = None,
    gpu_count: int = 1,
    engine: str = "vllm",
    provisioner: Provisioner | None = None,
    http_get: HttpGet | None = None,
    http_post: HttpPost | None = None,
) -> dict[str, Any]:
    provider_key = _normalize(provider)
    test_key = _normalize(test)
    spec = _find_spec(provider_key, test_key)
    if spec is None:
        raise ValueError(f"unknown smoke test: {provider_key} {test_key}")

    effective_env = env if env is not None else os.environ
    provision_price = _provisioned_price_per_hour(provider_key, test_key) if provisioned else None
    if provisioned:
        estimated_cost = provision_price or max(float(spec.estimated_cost_usd), 0.001)
    elif live:
        estimated_cost = float(spec.estimated_cost_usd)
    else:
        estimated_cost = 0.0
    coverage_depth = spec.coverage_depth
    functional_scope = spec.functional_scope
    if spec.category == "compute" and not provisioned:
        coverage_depth = "preflight"
        functional_scope = ("configuration",)
    mode = "provisioned" if provisioned else ("live" if live else "dry-run")
    recorder = SmokeRecorder(
        provider_key,
        test_key,
        mode=mode,
        estimated_cost_usd=estimated_cost,
        coverage_depth=coverage_depth,
        functional_scope=functional_scope,
    )
    recorder.event("smoke.started", category=spec.category)
    recorder.check(estimated_cost <= max_cost_usd, "cost ceiling check", ceiling=max_cost_usd, estimated=estimated_cost)
    _check_credentials(recorder, spec, effective_env)

    if base_url:
        recorder.event("endpoint.override", base_url=_sanitize_url(base_url))
    if model:
        recorder.event("model.override", model=model)

    if recorder.report["status"] != "pass":
        reason = (
            "preflight failed before live/provisioned action"
            if (live or provisioned)
            else "dry-run preflight failed"
        )
        recorder.event("live.skipped", reason=reason)
    elif spec.category == "multi-model":
        _run_multi_model_juggle_smoke(
            recorder,
            spec,
            env=effective_env,
            live=live,
            timeout=timeout,
            model=model,
            http_post=http_post or _default_http_post,
        )
    elif provisioned:
        _run_provisioned_model_smoke(
            recorder,
            spec,
            provider_key=provider_key,
            test_key=test_key,
            region=region,
            gpu_count=gpu_count,
            engine=engine,
            model=model,
            max_cost_usd=max_cost_usd,
            timeout=timeout,
            provisioner=provisioner or DeploymentManager(),
            http_post=http_post or _default_http_post,
            http_get=http_get or _default_http_get,
        )
    elif live:
        if test_key == "model-ping":
            _run_live_model_ping(
                recorder,
                spec,
                base_url=base_url,
                model=model,
                timeout=timeout,
                http_post=http_post or _default_http_post,
            )
        else:
            _run_live_metadata_probe(
                recorder,
                spec,
                base_url=base_url,
                timeout=timeout,
                http_get=http_get or _default_http_get,
            )
    else:
        recorder.event("live.skipped", reason="dry-run default")

    return recorder.finish()


def _build_registry() -> list[SmokeSpec]:
    specs: list[SmokeSpec] = [
        SmokeSpec(
            provider="multi-provider",
            test="model-juggle",
            category="multi-model",
            description="Plan or live-test several model API providers in one ordered smoke run.",
            required_env=tuple(),
            coverage_depth="orchestration",
            functional_scope=("provider_selection", "model_routing", "completion_aggregation"),
            estimated_cost_usd=0.008,
        ),
        SmokeSpec(
            provider="multi-platform",
            test="model-juggle",
            category="multi-model",
            description="Plan or live-test API and local model-serving platforms in one smoke run.",
            required_env=tuple(),
            coverage_depth="orchestration",
            functional_scope=("provider_selection", "platform_selection", "model_routing", "completion_aggregation"),
            estimated_cost_usd=0.006,
        ),
    ]
    for name, preset in PROVIDER_PRESETS.items():
        env_var = str(preset.get("credential_env_var") or "")
        api_base_url = str(preset.get("api_base_url") or "")
        endpoint = str(preset.get("free_models_endpoint") or "") or _models_endpoint(api_base_url)
        model = PROVIDER_FLAGSHIP_MODELS.get(name)
        model_required: tuple[tuple[str, ...], ...] = ((env_var,),) if env_var else tuple()
        specs.extend(
            [
                SmokeSpec(
                    provider=name,
                    test="credential-check",
                    category="model-api",
                    description="Validate credential variables and emit an evidence bundle without network or spend.",
                    required_env=model_required,
                    coverage_depth="configuration",
                    functional_scope=("credential_presence", "preset_shape"),
                    model=model,
                ),
                SmokeSpec(
                    provider=name,
                    test="metadata",
                    category="model-api",
                    description=(
                        "Cheap live metadata probe against the provider model-list endpoint "
                        "when --live is set."
                    ),
                    required_env=model_required,
                    coverage_depth="connectivity",
                    functional_scope=("credential_presence", "metadata_endpoint"),
                    endpoint=endpoint,
                    model=model,
                ),
                SmokeSpec(
                    provider=name,
                    test="model-ping",
                    category="model-api",
                    description="Preflight for a tiny prompt smoke; dry-run by default to avoid token spend.",
                    required_env=model_required,
                    coverage_depth="functional",
                    functional_scope=("credential_presence", "chat_request", "completion_parse"),
                    estimated_cost_usd=0.001,
                    endpoint=endpoint,
                    model=model,
                ),
            ]
        )

    compute_registry = ComputeProviderRegistry()
    for info in compute_registry.list_providers():
        provider = info.provider.value
        required = _credential_groups(provider)
        specs.append(
            SmokeSpec(
                provider=provider,
                test="credential-check",
                category="compute",
                description="Validate compute-provider credential variables without provisioning resources.",
                required_env=required,
            )
        )
        for gpu in sorted(info.pricing):
            specs.append(
                SmokeSpec(
                    provider=provider,
                    test=f"gpu-{_normalize(gpu)}",
                    category="compute",
                    description=(
                        f"GPU smoke for {info.display_name} {gpu}; dry-run preflight by default, "
                        "or provision/run/teardown when --provisioned is set."
                    ),
                    required_env=required,
                    coverage_depth="provisioned",
                    functional_scope=(
                        "credential_presence",
                        "terraform_deploy",
                        "model_task",
                        "teardown",
                    ),
                    estimated_cost_usd=float(info.pricing.get(gpu, 0.0) or 0.0),
                )
            )
        if provider == "aws":
            specs.append(
                SmokeSpec(
                    provider="aws",
                    test="ec2-a100",
                    category="compute",
                    description=(
                        "AWS EC2 A100 smoke alias; dry-run preflight by default, "
                        "or provision/run/teardown when --provisioned is set."
                    ),
                    required_env=_credential_groups("aws"),
                    coverage_depth="provisioned",
                    functional_scope=(
                        "credential_presence",
                        "terraform_deploy",
                        "model_task",
                        "teardown",
                    ),
                    estimated_cost_usd=float(info.pricing.get("a100_80", 0.0) or 0.0),
                )
            )

    existing_compute = {spec.provider for spec in specs if spec.category == "compute"}
    for provider, display_name in (
        ("qemu", "Local QEMU"),
        ("vast", "Vast.ai"),
        ("vsphere", "VMware vSphere"),
    ):
        if provider in existing_compute:
            continue
        specs.append(
            SmokeSpec(
                provider=provider,
                test="credential-check",
                category="compute",
                description=(
                    f"Validate {display_name} compute-provider configuration "
                    "without provisioning resources."
                ),
                required_env=_credential_groups(provider),
                coverage_depth="configuration",
                functional_scope=("credential_presence", "provider_alias"),
            )
        )
    for provider, env_vars in _LOCAL_BACKEND_SMOKES.items():
        specs.extend(
            [
                SmokeSpec(
                    provider=provider,
                    test="metadata",
                    category="local-model",
                    description="Probe a local or cluster OpenAI-compatible metadata endpoint when --live is set.",
                    required_env=tuple((name,) for name in env_vars),
                    endpoint=f"${env_vars[0]}/v1/models",
                ),
                SmokeSpec(
                    provider=provider,
                    test="model-ping",
                    category="local-model",
                    description="Preflight a zero-token-spend local model ping; use --live for metadata readiness.",
                    required_env=tuple((name,) for name in env_vars),
                ),
            ]
        )

    for provider, env_vars in _COMMON_CONNECTOR_SMOKES.items():
        specs.append(
            SmokeSpec(
                provider=provider,
                test="credential-check",
                category="connector",
                description="Validate connector configuration variables and emit third-party friendly diagnostics.",
                required_env=tuple((name,) for name in env_vars),
            )
        )
    return _dedupe_specs(specs)


def _dedupe_specs(specs: list[SmokeSpec]) -> list[SmokeSpec]:
    seen: set[tuple[str, str]] = set()
    result: list[SmokeSpec] = []
    for spec in specs:
        key = (spec.provider, spec.test)
        if key in seen:
            continue
        seen.add(key)
        result.append(spec)
    return result


def _find_spec(provider: str, test: str) -> SmokeSpec | None:
    for spec in _build_registry():
        if spec.provider == provider and spec.test == test:
            return spec
    return None


def _credential_groups(provider: str) -> tuple[tuple[str, ...], ...]:
    return _CREDENTIAL_ALIASES.get(provider, tuple())


def _analysis_prompt(provider: str, test: str) -> str:
    return (
        f"Analyze this Gludd smoke report for {provider} {test}. "
        "Use trace_id, coverage_depth, functional_scope, status, metrics, ordered trace, "
        "events, logs, endpoint_diagnostics, and expected tunables. Classify the failure as "
        "configuration, auth_rejected, "
        "network_endpoint, provider_contract, cost_guard, missing_full_probe, or code_bug. "
        "Do not ask for secrets. Redact any accidental credentials. Identify the first "
        "failing trace sequence, the expected component behavior, the likely code path, "
        "and the focused tests or provider docs needed to fix it."
    )


def _check_credentials(recorder: SmokeRecorder, spec: SmokeSpec, env: Mapping[str, str]) -> None:
    if not spec.required_env:
        recorder.check(True, "no credential variables required for this preflight")
        recorder.event("credentials.validated", present=[])
        return

    missing: list[str] = []
    present: list[str] = []
    for aliases in spec.required_env:
        found = next((name for name in aliases if env.get(name)), None)
        if found:
            present.append(found)
        else:
            missing.append("/".join(aliases))

    recorder.check(not missing, "credential variable check", present=present, missing=missing)
    if missing:
        recorder.event("credentials.missing", missing=missing)
    else:
        recorder.event("credentials.validated", present=present)


def _run_live_metadata_probe(
    recorder: SmokeRecorder,
    spec: SmokeSpec,
    *,
    base_url: str | None,
    timeout: float,
    http_get: HttpGet,
) -> None:
    endpoint = base_url or spec.endpoint
    if not endpoint:
        recorder.event("live.skipped", reason="no metadata endpoint registered")
        return
    endpoint = _expand_endpoint(endpoint)
    recorder.event("http.request", method="GET", url=_sanitize_url(endpoint), timeout=timeout)
    try:
        response = http_get(endpoint, timeout)
    except Exception as exc:
        recorder.report["metrics"]["http_requests"] += 1
        recorder.check(False, "metadata endpoint request failed", error=type(exc).__name__)
        recorder.event("http.error", error=type(exc).__name__)
        return

    recorder.report["metrics"]["http_requests"] += 1
    status_code = int(getattr(response, "status_code", 0) or 0)
    elapsed_ms = _elapsed_ms(response)
    recorder.event("http.response", status_code=status_code, elapsed_ms=elapsed_ms)
    if status_code in {401, 403}:
        recorder.report["status"] = "auth_rejected"
        recorder.report["metrics"]["auth_rejected"] = 1
        recorder.report["metrics"]["checks_failed"] += 1
        recorder.event("auth.rejected", status_code=status_code)
        recorder.log("warning", "metadata endpoint rejected credentials", status_code=status_code)
        return
    recorder.check(
        200 <= status_code < 300,
        "metadata endpoint returned a successful response",
        status_code=status_code,
    )
    if not 200 <= status_code < 300:
        return

    models_seen = _count_models(response)
    recorder.report["metrics"]["models_seen"] = models_seen
    if models_seen:
        recorder.log("info", "metadata endpoint returned model entries", models_seen=models_seen)


def _run_multi_model_juggle_smoke(
    recorder: SmokeRecorder,
    spec: SmokeSpec,
    *,
    env: Mapping[str, str],
    live: bool,
    timeout: float,
    model: str | None,
    http_post: HttpPost,
) -> None:
    plan = _build_multi_model_juggle_plan(spec, env, model)
    public_plan = [_public_juggle_leg(leg) for leg in plan]
    recorder.report["model_juggle"] = {"plan": public_plan, "results": []}

    providers = {str(leg["provider"]) for leg in plan}
    platforms = {str(leg["platform"]) for leg in plan}
    models = {str(leg["model"]) for leg in plan}
    recorder.report["metrics"]["juggle_planned_legs"] = len(plan)
    recorder.report["metrics"]["juggle_unique_providers"] = len(providers)
    recorder.report["metrics"]["juggle_unique_platforms"] = len(platforms)
    recorder.report["metrics"]["juggle_unique_models"] = len(models)

    for index, leg in enumerate(public_plan, start=1):
        recorder.event("model.juggle.plan", index=index, **leg)

    if spec.provider == "multi-provider":
        recorder.check(
            len(providers) >= 3,
            "multi-provider smoke planned at least three model providers",
            providers=sorted(providers),
        )
    else:
        recorder.check(
            len(platforms) >= 2,
            "multi-platform smoke planned multiple serving platforms",
            platforms=sorted(platforms),
        )

    if not live:
        recorder.event("live.skipped", reason="multi-model dry-run plan only")
        return

    configured = [leg for leg in plan if bool(leg.get("configured"))]
    recorder.report["metrics"]["juggle_configured_legs"] = len(configured)
    recorder.check(
        len(configured) >= 2,
        "multi-model live run has at least two configured legs",
        configured=[_public_juggle_leg(leg) for leg in configured],
    )
    if len(configured) < 2:
        recorder.event("model.juggle.skipped", reason="fewer than two configured live legs")
        return

    successes = 0
    for index, leg in enumerate(configured, start=1):
        successes += int(
            _run_multi_model_juggle_leg(
                recorder,
                index=index,
                leg=leg,
                env=env,
                timeout=timeout,
                http_post=http_post,
            )
        )
    recorder.report["metrics"]["juggle_successful_legs"] = successes
    recorder.report["metrics"]["completion_seen"] = int(successes > 0)
    recorder.check(
        successes == len(configured),
        "all configured model juggle legs completed",
        successes=successes,
        configured=len(configured),
    )


def _build_multi_model_juggle_plan(
    spec: SmokeSpec,
    env: Mapping[str, str],
    model: str | None,
) -> list[dict[str, object]]:
    if spec.provider == "multi-provider":
        return [_juggle_provider_leg(provider, env, model) for provider in MULTI_PROVIDER_CANDIDATES]

    provider_legs = [_juggle_provider_leg(provider, env, model) for provider in MULTI_PLATFORM_API_CANDIDATES]
    local_legs = [_juggle_local_leg(provider, env, model) for provider in MULTI_PLATFORM_LOCAL_CANDIDATES]
    return provider_legs + local_legs


def _juggle_provider_leg(provider: str, env: Mapping[str, str], model: str | None) -> dict[str, object]:
    preset = PROVIDER_PRESETS[provider]
    credential_env = str(preset.get("credential_env_var") or "")
    api_base_url = str(preset.get("api_base_url") or "")
    endpoint = str(preset.get("free_models_endpoint") or "") or _models_endpoint(api_base_url) or api_base_url
    selected_model = model or PROVIDER_FLAGSHIP_MODELS.get(provider) or "default"
    return {
        "provider": provider,
        "platform": "model-api",
        "endpoint": endpoint,
        "model": selected_model,
        "credential_env": credential_env,
        "configured": bool(credential_env and env.get(credential_env)),
    }


def _juggle_local_leg(provider: str, env: Mapping[str, str], model: str | None) -> dict[str, object]:
    endpoint_env = _LOCAL_BACKEND_SMOKES[provider][0]
    endpoint = env.get(endpoint_env) or f"ENV:{endpoint_env}"
    model_env = f"{provider.upper().replace('-', '_')}_MODEL"
    selected_model = model or env.get(model_env) or env.get("GLUDD_SMOKE_LOCAL_MODEL") or "local-smoke-model"
    return {
        "provider": provider,
        "platform": "local-model",
        "endpoint": endpoint,
        "model": selected_model,
        "credential_env": "",
        "configured": bool(env.get(endpoint_env)),
    }


def _public_juggle_leg(leg: Mapping[str, object]) -> dict[str, object]:
    return {
        "provider": str(leg.get("provider") or ""),
        "platform": str(leg.get("platform") or ""),
        "endpoint": _sanitize_url(str(leg.get("endpoint") or "")),
        "model": str(leg.get("model") or ""),
        "credential_env": str(leg.get("credential_env") or ""),
        "configured": bool(leg.get("configured")),
    }


def _run_multi_model_juggle_leg(
    recorder: SmokeRecorder,
    *,
    index: int,
    leg: Mapping[str, object],
    env: Mapping[str, str],
    timeout: float,
    http_post: HttpPost,
) -> bool:
    provider = str(leg.get("provider") or "")
    platform = str(leg.get("platform") or "")
    model = str(leg.get("model") or "")
    endpoint = _chat_completions_endpoint(str(leg.get("endpoint") or ""))
    credential_env = str(leg.get("credential_env") or "")
    credential = env.get(credential_env) if credential_env else None
    headers = {"content-type": "application/json"}
    if credential:
        headers["Authorization"] = f"Bearer {credential}"
    payload: dict[str, object] = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with OK."}],
        "max_tokens": 1,
        "temperature": 0,
        "stream": False,
    }
    result: dict[str, object] = {
        "index": index,
        "provider": provider,
        "platform": platform,
        "endpoint": _sanitize_url(endpoint),
        "model": model,
        "status": "fail",
    }
    recorder.event(
        "model.juggle.request",
        index=index,
        provider=provider,
        platform=platform,
        endpoint=_sanitize_url(endpoint),
        model=model,
    )
    try:
        response = http_post(endpoint, headers, payload, timeout)
    except Exception as exc:
        recorder.report["metrics"]["http_requests"] += 1
        result["error"] = type(exc).__name__
        recorder.report["model_juggle"]["results"].append(result)
        recorder.check(False, "model juggle leg request failed", provider=provider, error=type(exc).__name__)
        recorder.event("model.juggle.error", index=index, provider=provider, error=type(exc).__name__)
        return False

    recorder.report["metrics"]["http_requests"] += 1
    status_code = int(getattr(response, "status_code", 0) or 0)
    completion_seen = _has_completion(_response_json(response))
    result["status_code"] = status_code
    result["completion_seen"] = completion_seen
    result["elapsed_ms"] = _elapsed_ms(response)
    if status_code in {401, 403}:
        result["status"] = "auth_rejected"
        recorder.report["metrics"]["auth_rejected"] = int(recorder.report["metrics"].get("auth_rejected", 0)) + 1
    elif 200 <= status_code < 300 and completion_seen:
        result["status"] = "pass"
    recorder.report["model_juggle"]["results"].append(result)
    recorder.event(
        "model.juggle.response",
        index=index,
        provider=provider,
        platform=platform,
        status_code=status_code,
        completion_seen=completion_seen,
        elapsed_ms=result["elapsed_ms"],
    )
    ok = result["status"] == "pass"
    recorder.check(
        ok,
        "model juggle leg returned completion",
        provider=provider,
        platform=platform,
        status_code=status_code,
        completion_seen=completion_seen,
    )
    return ok


def _run_provisioned_model_smoke(
    recorder: SmokeRecorder,
    spec: SmokeSpec,
    *,
    provider_key: str,
    test_key: str,
    region: str | None,
    gpu_count: int,
    engine: str,
    model: str | None,
    max_cost_usd: float,
    timeout: float,
    provisioner: Provisioner,
    http_post: HttpPost,
    http_get: HttpGet,
) -> None:
    selected_gpu = _gpu_for_smoke_test(test_key)
    selected_model = model or spec.model or DEFAULT_PROVISIONED_MODEL
    config = ComputeConfig(
        provider=ComputeProvider(provider_key),
        gpu_type=selected_gpu,
        gpu_count=gpu_count,
        engine=InferenceEngine(engine),
        model_name=selected_model,
        region=region,
        max_cost_usd=max_cost_usd,
        timeout_minutes=max(timeout / 60.0, 1.0),
    )
    recorder.event(
        "provision.request",
        provider=provider_key,
        gpu_type=selected_gpu.value,
        gpu_count=gpu_count,
        engine=engine,
        model=selected_model,
        region=region,
        max_cost_usd=max_cost_usd,
    )
    instance: ComputeInstance | None = None
    try:
        instance = asyncio.run(provisioner.deploy(config))
        recorder.event(
            "provision.ready",
            instance_id=instance.instance_id,
            provider=instance.provider.value,
            gpu_type=instance.gpu_type.value,
            endpoint_url=_sanitize_url(instance.endpoint_url or ""),
        )
        endpoint = instance.endpoint_url
        if not endpoint:
            recorder.check(False, "provisioned instance did not expose an endpoint", instance_id=instance.instance_id)
            return
        _run_live_model_ping(
            recorder,
            spec,
            base_url=endpoint,
            model=selected_model,
            timeout=timeout,
            http_post=http_post,
            require_credential=False,
        )
        _probe_provisioned_endpoint_diagnostics(
            recorder,
            endpoint=endpoint,
            selected_model=selected_model,
            config=config,
            timeout=timeout,
            http_get=http_get,
        )
        if recorder.report["status"] == "pass" and recorder.report["metrics"].get("completion_seen") == 1:
            recorder.event("model.task.verified", task="one_token_ok_completion")
    except Exception as exc:
        recorder.check(False, "provisioned smoke failed", error=type(exc).__name__)
        recorder.event("provision.error", error=type(exc).__name__)
    finally:
        if instance is None:
            recorder.event("provision.teardown.skipped", reason="no instance id")
        else:
            recorder.event("provision.teardown.start", instance_id=instance.instance_id)
            try:
                asyncio.run(provisioner.destroy(instance.instance_id))
            except Exception as exc:
                recorder.report["status"] = "fail"
                recorder.report["metrics"]["checks_failed"] += 1
                recorder.event(
                    "provision.teardown.failed",
                    instance_id=instance.instance_id,
                    error=type(exc).__name__,
                )
                recorder.log(
                    "error",
                    "provisioned resource teardown failed",
                    instance_id=instance.instance_id,
                    error=type(exc).__name__,
                )
            else:
                recorder.event("provision.teardown.complete", instance_id=instance.instance_id)


def _gpu_for_smoke_test(test_key: str) -> GPUType:
    if test_key == "ec2-a100":
        return GPUType.A100_80
    if test_key.startswith("gpu-"):
        gpu_name = test_key.removeprefix("gpu-").replace("-", "_")
        try:
            return GPUType(gpu_name)
        except ValueError:
            return GPUType.T4
    return GPUType.T4


def _provisioned_price_per_hour(provider_key: str, test_key: str) -> float | None:
    try:
        provider = ComputeProvider(provider_key)
    except ValueError:
        return None
    try:
        info = ComputeProviderRegistry().get(provider)
    except KeyError:
        return None
    return _price_for_smoke_test(info, test_key)


def _price_for_smoke_test(info: ProviderInfo, test_key: str) -> float | None:
    gpu_key = _gpu_for_smoke_test(test_key).value
    return info.pricing.get(gpu_key)


def _probe_provisioned_endpoint_diagnostics(
    recorder: SmokeRecorder,
    *,
    endpoint: str,
    selected_model: str,
    config: ComputeConfig,
    timeout: float,
    http_get: HttpGet,
) -> None:
    diagnostics: dict[str, Any] = {
        "expected": _expected_provisioned_tunables(config),
        "health": {},
        "models": {},
        "metrics": {},
    }
    recorder.report["endpoint_diagnostics"] = diagnostics

    health = _probe_endpoint(
        recorder,
        kind="health",
        url=_health_endpoint(endpoint),
        timeout=timeout,
        http_get=http_get,
    )
    diagnostics["health"] = _redact({key: value for key, value in health.items() if key != "json"})
    recorder.check(
        _probe_status_ok(health),
        "serving health endpoint returned successfully",
        status_code=health.get("status_code"),
    )

    models = _probe_endpoint(
        recorder,
        kind="models",
        url=_models_probe_endpoint(endpoint),
        timeout=timeout,
        http_get=http_get,
    )
    model_ids = _extract_model_ids(models.get("json"))
    diagnostics["models"] = {
        "status_code": models.get("status_code"),
        "elapsed_ms": models.get("elapsed_ms"),
        "model_ids": model_ids,
        "model_count": len(model_ids),
        "body_sample": _redact(models.get("text")),
    }
    recorder.report["metrics"]["models_seen"] = len(model_ids)
    recorder.check(
        _probe_status_ok(models) and selected_model in model_ids,
        "model registry returned the provisioned model identifier",
        model=selected_model,
        model_ids=model_ids,
        status_code=models.get("status_code"),
    )

    metrics = _probe_endpoint(
        recorder,
        kind="metrics",
        url=_metrics_endpoint(endpoint),
        timeout=timeout,
        http_get=http_get,
    )
    metric_names = _extract_metric_names(str(metrics.get("raw_text") or ""))
    engine_metric_seen = _engine_metric_seen(config.engine, metric_names)
    diagnostics["metrics"] = _redact(
        {
            "status_code": metrics.get("status_code"),
            "elapsed_ms": metrics.get("elapsed_ms"),
            "metric_count": len(metric_names),
            "metric_names": metric_names[:50],
            "body_sample": metrics.get("text"),
            "engine_metric_seen": engine_metric_seen,
        }
    )
    recorder.report["metrics"]["serving_metric_names"] = len(metric_names)
    recorder.report["metrics"]["process_metrics_seen"] = int(
        any(name.startswith("process_") for name in metric_names)
    )
    recorder.report["metrics"]["engine_metrics_seen"] = int(engine_metric_seen)
    recorder.check(
        _probe_status_ok(metrics) and bool(metric_names),
        "serving metrics endpoint returned Prometheus metric names",
        status_code=metrics.get("status_code"),
        metric_count=len(metric_names),
    )
    recorder.check(
        engine_metric_seen,
        "serving metrics identify the configured inference engine",
        engine=config.engine.value,
        metric_names=metric_names[:20],
    )


def _expected_provisioned_tunables(config: ComputeConfig) -> dict[str, object]:
    return {
        "provider": config.provider.value,
        "gpu_type": config.gpu_type.value,
        "gpu_count": config.gpu_count,
        "engine": config.engine.value,
        "model_name": config.model_name,
        "region": config.region,
        "spot": config.spot,
        "max_cost_usd": config.max_cost_usd,
        "timeout_minutes": config.timeout_minutes,
        "disk_size_gb": config.disk_size_gb,
        "container_image": config.container_image,
        "deploy_type": config.deploy_type,
        "allowed_cidr": config.allowed_cidr,
        "guided_decoding_backend": config.guided_decoding_backend,
        "enable_structured_outputs": config.enable_structured_outputs,
        "grammar_file": config.grammar_file,
        "vsphere_verify_ssl": config.vsphere_verify_ssl,
        "workload_type": config.workload_type,
        "deployment_profile": config.deployment_profile or {},
    }


def _probe_endpoint(
    recorder: SmokeRecorder,
    *,
    kind: str,
    url: str,
    timeout: float,
    http_get: HttpGet,
) -> dict[str, Any]:
    recorder.event("endpoint.probe.request", kind=kind, method="GET", url=_sanitize_url(url), timeout=timeout)
    try:
        response = http_get(url, timeout)
    except Exception as exc:
        recorder.report["metrics"]["http_requests"] += 1
        recorder.event("endpoint.probe.error", kind=kind, error=type(exc).__name__)
        recorder.log("error", f"{kind} endpoint probe failed", kind=kind, error=type(exc).__name__)
        return {"status_code": None, "elapsed_ms": None, "json": None, "text": "", "raw_text": ""}

    recorder.report["metrics"]["http_requests"] += 1
    status_code = int(getattr(response, "status_code", 0) or 0)
    raw_text = _response_text(response)
    result = {
        "status_code": status_code,
        "elapsed_ms": _elapsed_ms(response),
        "json": _response_json(response),
        "text": _text_snippet(raw_text),
        "raw_text": raw_text,
    }
    recorder.event(
        "endpoint.probe.response",
        kind=kind,
        status_code=status_code,
        elapsed_ms=result["elapsed_ms"],
        bytes=len(raw_text.encode("utf-8")),
    )
    return result


def _probe_status_ok(result: Mapping[str, Any]) -> bool:
    status_code = result.get("status_code")
    return isinstance(status_code, int) and 200 <= status_code < 300


def _chat_completions_endpoint(endpoint: str) -> str:
    expanded = _expand_endpoint(endpoint).rstrip("/")
    if expanded.endswith("/chat/completions"):
        return expanded
    return f"{_endpoint_root(expanded)}/v1/chat/completions"


def _models_probe_endpoint(endpoint: str) -> str:
    return f"{_endpoint_root(endpoint)}/v1/models"


def _health_endpoint(endpoint: str) -> str:
    return f"{_endpoint_root(endpoint)}/health"


def _metrics_endpoint(endpoint: str) -> str:
    return f"{_endpoint_root(endpoint)}/metrics"


def _endpoint_root(endpoint: str) -> str:
    expanded = _expand_endpoint(endpoint).rstrip("/")
    for suffix in ("/v1/chat/completions", "/chat/completions", "/v1/models", "/models", "/v1"):
        if expanded.endswith(suffix):
            return expanded[: -len(suffix)].rstrip("/")
    return expanded


def _response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text
    payload = _response_json(response)
    if payload is None:
        return ""
    try:
        return json.dumps(payload, sort_keys=True)
    except TypeError:
        return str(payload)


def _text_snippet(value: str, limit: int = 2000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "...<truncated>"


def _extract_model_ids(payload: object) -> list[str]:
    model_ids: list[str] = []
    seen: set[str] = set()

    def add(value: object) -> None:
        if isinstance(value, str) and value and value not in seen:
            seen.add(value)
            model_ids.append(value)

    def visit(value: object) -> None:
        if isinstance(value, Mapping):
            add(value.get("id"))
            add(value.get("model"))
            add(value.get("name"))
            for key in ("data", "models", "items"):
                items = value.get(key)
                if isinstance(items, list):
                    for item in items:
                        visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    return model_ids


def _extract_metric_names(text: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name = line.split(None, 1)[0].split("{", 1)[0]
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _engine_metric_seen(engine: InferenceEngine, metric_names: list[str]) -> bool:
    lowered = [name.lower() for name in metric_names]
    if engine == InferenceEngine.VLLM:
        return any(name.startswith("vllm") for name in lowered)
    if engine == InferenceEngine.LLAMACPP:
        return any(name.startswith("llama") or "llamacpp" in name for name in lowered)
    return False


def _run_live_model_ping(
    recorder: SmokeRecorder,
    spec: SmokeSpec,
    *,
    base_url: str | None,
    model: str | None,
    timeout: float,
    http_post: HttpPost,
    require_credential: bool = True,
) -> None:
    endpoint = base_url or spec.endpoint
    selected_model = model or spec.model
    credential_env = spec.required_env[0][0] if spec.required_env else ""
    credential = os.environ.get(credential_env) if credential_env else None
    credential_ok = bool(credential) or not require_credential
    if not endpoint or not selected_model or not credential_ok:
        recorder.check(
            False,
            "functional model ping is not fully configured",
            endpoint=bool(endpoint),
            model=bool(selected_model),
            credential=credential_ok,
        )
        recorder.event("probe.unimplemented", reason="missing endpoint, model, or credential")
        return
    endpoint = _chat_completions_endpoint(endpoint)
    payload: dict[str, object] = {
        "model": selected_model,
        "messages": [{"role": "user", "content": "Reply with OK."}],
        "max_tokens": 1,
        "temperature": 0,
        "stream": False,
    }
    headers = {"content-type": "application/json"}
    if credential:
        headers["Authorization"] = f"Bearer {credential}"
    recorder.event("http.request", method="POST", url=_sanitize_url(endpoint), timeout=timeout, model=selected_model)
    try:
        response = http_post(endpoint, headers, payload, timeout)
    except Exception as exc:
        recorder.report["metrics"]["http_requests"] += 1
        recorder.check(False, "model ping request failed", error=type(exc).__name__)
        recorder.event("http.error", error=type(exc).__name__)
        return
    recorder.report["metrics"]["http_requests"] += 1
    status_code = int(getattr(response, "status_code", 0) or 0)
    recorder.event("http.response", status_code=status_code, elapsed_ms=_elapsed_ms(response))
    if status_code in {401, 403}:
        recorder.report["status"] = "auth_rejected"
        recorder.report["metrics"]["auth_rejected"] = 1
        recorder.report["metrics"]["checks_failed"] += 1
        recorder.event("auth.rejected", status_code=status_code)
        recorder.log("warning", "model ping rejected credentials", status_code=status_code)
        return
    recorder.check(200 <= status_code < 300, "model ping returned a successful response", status_code=status_code)
    if not 200 <= status_code < 300:
        return
    payload_json = _response_json(response)
    completion_seen = _has_completion(payload_json)
    recorder.report["metrics"]["completion_seen"] = int(completion_seen)
    recorder.check(completion_seen, "model ping returned completion content", model=selected_model)


def _default_http_get(url: str, timeout: float) -> httpx.Response:
    return httpx.get(url, timeout=timeout)


def _default_http_post(
    url: str,
    headers: dict[str, str],
    json_payload: dict[str, object],
    timeout: float,
) -> httpx.Response:
    return httpx.post(url, headers=headers, json=json_payload, timeout=timeout)


def _models_endpoint(api_base_url: str) -> str | None:
    if not api_base_url:
        return None
    if api_base_url.rstrip("/").endswith("/chat/completions"):
        return None
    return api_base_url.rstrip("/") + "/models"


def _expand_endpoint(endpoint: str) -> str:
    if endpoint.startswith("$"):
        env_name, _, suffix = endpoint[1:].partition("/")
        base = os.environ.get(env_name, "").rstrip("/")
        return f"{base}/{suffix}" if base and suffix else base
    return endpoint


def _response_json(response: Any) -> object:
    try:
        return response.json()
    except Exception:
        return None


def _has_completion(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        return True
    content = payload.get("content")
    return isinstance(content, list) and bool(content)


def _count_models(response: Any) -> int:
    data = _response_json(response)
    if data is None:
        return 0
    if isinstance(data, dict):
        items = data.get("data") or data.get("models") or data.get("items")
        if isinstance(items, list):
            return len(items)
    if isinstance(data, list):
        return len(data)
    return 0


def _elapsed_ms(response: Any) -> float | None:
    explicit = getattr(response, "elapsed_ms", None)
    if explicit is not None:
        return float(explicit)
    elapsed = getattr(response, "elapsed", None)
    if elapsed is not None and hasattr(elapsed, "total_seconds"):
        return float(elapsed.total_seconds() * 1000)
    return None


def _normalize(value: str) -> str:
    return value.strip().lower().replace("_", "-")


def _redact(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(k): _redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact(item) for item in value)
    if isinstance(value, str) and _looks_secret(value):
        return "<redacted>"
    return value


def _looks_secret(value: str) -> bool:
    lowered = value.lower()
    if any(marker in lowered for marker in ("secret", "token", "password", "akia")):
        return True
    return len(value) >= 20 and any(ch.isdigit() for ch in value) and any(ch.isalpha() for ch in value)


def _sanitize_url(url: str) -> str:
    try:
        parsed = httpx.URL(url)
    except Exception:
        return "<invalid-url>"
    rendered = str(parsed)
    if not parsed.username and not parsed.password:
        return rendered
    prefix, scheme_separator, rest = rendered.partition("//")
    if not scheme_separator:
        return rendered
    _, separator, host_and_path = rest.partition("@")
    if not separator:
        return rendered
    return f"{prefix}{scheme_separator}{host_and_path}"
