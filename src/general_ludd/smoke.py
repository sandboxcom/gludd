"""Low-cost provider smoke-test registry and runner.

The smoke runner is intentionally conservative: default runs validate local
configuration and emit a complete evidence bundle without provisioning cloud
resources or making model calls. Operators opt into live HTTP probes with the
CLI flag, and those probes are limited to cheap metadata endpoints.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from general_ludd.infra.providers import ProviderRegistry as ComputeProviderRegistry
from general_ludd.models.provider_presets import (
    PROVIDER_FLAGSHIP_MODELS,
    PROVIDER_PRESETS,
)

HttpGet = Callable[[str, float], Any]


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
    "kubernetes": (("KUBECONFIG",),),
    "vmware": (("VSPHERE_USER",), ("VSPHERE_PASSWORD",), ("VSPHERE_SERVER",)),
}


@dataclass(frozen=True)
class SmokeSpec:
    provider: str
    test: str
    category: str
    description: str
    required_env: tuple[tuple[str, ...], ...]
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
            "estimated_cost_usd": self.estimated_cost_usd,
            "default_live": self.default_live,
            "endpoint": self.endpoint,
            "model": self.model,
        }


class SmokeRecorder:
    def __init__(self, provider: str, test: str, *, live: bool, estimated_cost_usd: float) -> None:
        self.started = time.monotonic()
        self.report: dict[str, Any] = {
            "run_id": f"smoke-{uuid.uuid4().hex[:12]}",
            "provider": provider,
            "test": test,
            "mode": "live" if live else "dry-run",
            "started_at": datetime.now(UTC).isoformat(),
            "completed_at": None,
            "status": "pass",
            "estimated_cost_usd": estimated_cost_usd,
            "logs": [],
            "metrics": {
                "checks_total": 0,
                "checks_failed": 0,
                "http_requests": 0,
                "models_seen": 0,
                "duration_ms": 0,
            },
            "events": [],
        }

    def log(self, level: str, message: str, **fields: object) -> None:
        self.report["logs"].append(
            {
                "ts": datetime.now(UTC).isoformat(),
                "level": level,
                "message": message,
                "fields": _redact(fields),
            }
        )

    def event(self, name: str, **fields: object) -> None:
        self.report["events"].append(
            {
                "ts": datetime.now(UTC).isoformat(),
                "name": name,
                "fields": _redact(fields),
            }
        )

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
    http_get: HttpGet | None = None,
) -> dict[str, Any]:
    provider_key = _normalize(provider)
    test_key = _normalize(test)
    spec = _find_spec(provider_key, test_key)
    if spec is None:
        raise ValueError(f"unknown smoke test: {provider_key} {test_key}")

    effective_env = env if env is not None else os.environ
    estimated_cost = float(spec.estimated_cost_usd) if live else 0.0
    recorder = SmokeRecorder(provider_key, test_key, live=live, estimated_cost_usd=estimated_cost)
    recorder.event("smoke.started", category=spec.category)
    recorder.check(estimated_cost <= max_cost_usd, "cost ceiling check", ceiling=max_cost_usd, estimated=estimated_cost)
    _check_credentials(recorder, spec, effective_env)

    if base_url:
        recorder.event("endpoint.override", base_url=_sanitize_url(base_url))
    if model:
        recorder.event("model.override", model=model)

    if live and recorder.report["status"] == "pass":
        _run_live_metadata_probe(
            recorder,
            spec,
            base_url=base_url,
            timeout=timeout,
            http_get=http_get or _default_http_get,
        )
    else:
        recorder.event("live.skipped", reason="dry-run default" if not live else "preflight failed")

    return recorder.finish()


def _build_registry() -> list[SmokeSpec]:
    specs: list[SmokeSpec] = []
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
                    endpoint=endpoint,
                    model=model,
                ),
                SmokeSpec(
                    provider=name,
                    test="model-ping",
                    category="model-api",
                    description="Preflight for a tiny prompt smoke; dry-run by default to avoid token spend.",
                    required_env=model_required,
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
                    test=f"gpu-{gpu}",
                    category="compute",
                    description=f"Preflight {info.display_name} GPU smoke for {gpu}; no instance is created.",
                    required_env=required,
                )
            )
        if provider == "aws":
            specs.append(
                SmokeSpec(
                    provider="aws",
                    test="ec2-a100",
                    category="compute",
                    description="AWS EC2 A100 preflight alias for gpu-a100_80; no instance is created.",
                    required_env=_credential_groups("aws"),
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
    recorder.check(200 <= status_code < 500, "metadata endpoint returned a bounded response", status_code=status_code)

    models_seen = _count_models(response)
    recorder.report["metrics"]["models_seen"] = models_seen
    if models_seen:
        recorder.log("info", "metadata endpoint returned model entries", models_seen=models_seen)


def _default_http_get(url: str, timeout: float) -> httpx.Response:
    return httpx.get(url, timeout=timeout)


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


def _count_models(response: Any) -> int:
    try:
        data = response.json()
    except Exception:
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
    if parsed.password or parsed.username:
        return str(parsed.copy_with(username="", password=""))
    return str(parsed)
