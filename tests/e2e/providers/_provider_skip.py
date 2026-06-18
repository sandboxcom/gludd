"""Shared skip helpers for provider E2E tests.

Reads backend configuration from environment variables and .secrets/llm_keys.env.
Exposes skip_unless(provider) and require_backend(env_var) which call
pytest.skip() with a clear human-readable reason when a backend is absent or
unreachable.

SECURITY: Never log or print secret values. Only backend URLs/hostnames are
used in skip messages.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

# ---------------------------------------------------------------------------
# Environment variable names for each backend
# ---------------------------------------------------------------------------

BACKEND_ENV_VARS: dict[str, str] = {
    "ollama": "OLLAMA_BASE_URL",
    "vllm": "VLLM_BASE_URL",
    "llamacpp": "LLAMACPP_BASE_URL",
    "slurm": "SLURM_HOST",  # or SLURM_REST_URL for REST mode
    "azure": "AZURE_BASE_URL",
}

# Whether the operator has opted in to allowing local model base URLs through
# the SSRF guard. See DESIGN_local_cloud_providers_e2e.md §2.1(A).
ALLOW_LOCAL_MODEL_BASE_URLS: bool = (
    os.environ.get("GLUDD_ALLOW_LOCAL_MODEL_BASE_URLS") == "1"
)


# ---------------------------------------------------------------------------
# Secrets loader (reads .secrets/llm_keys.env but never leaks values)
# ---------------------------------------------------------------------------

def _load_secrets_file() -> dict[str, str]:
    """Load .secrets/llm_keys.env from repo root if present.

    Returns a dict of key->value. Never raises; returns {} on any error.
    Values are NOT logged or printed anywhere.
    """
    # Walk up from this file to find repo root (contains pyproject.toml)
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / "pyproject.toml").exists():
            secrets_file = parent / ".secrets" / "llm_keys.env"
            break
    else:
        return {}

    if not secrets_file.exists():
        return {}

    result: dict[str, str] = {}
    try:
        for line in secrets_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, val = line.partition("=")
                result[key.strip()] = val.strip()
    except OSError:
        pass
    return result


def _get_env_with_secrets(key: str) -> str | None:
    """Read a value from env first, then .secrets/llm_keys.env as fallback."""
    val = os.environ.get(key)
    if val:
        return val
    secrets = _load_secrets_file()
    return secrets.get(key)


# ---------------------------------------------------------------------------
# Reachability probe
# ---------------------------------------------------------------------------

def _http_alive(
    url: str,
    path: str = "/v1/models",
    timeout: float = 2.0,
) -> tuple[bool, str]:
    """Probe url+path with a cheap GET. Returns (is_alive, reason_string).

    Never raises. Best-effort: any exception is treated as unreachable.
    """
    try:
        probe_url = url.rstrip("/") + path
        r = httpx.get(probe_url, timeout=timeout)
        return (r.status_code < 500, f"HTTP {r.status_code}")
    except Exception as exc:
        return (False, f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Core skip helpers
# ---------------------------------------------------------------------------

def require_backend(
    env_var: str,
    path: str = "/v1/models",
    *,
    probe: bool = True,
) -> str:
    """Return base_url or call pytest.skip() with a precise reason.

    Two gates (both must pass):
    1. Configured  — env_var is set (in environment or .secrets/llm_keys.env).
    2. Reachable   — a cheap HTTP liveness probe succeeds (skipped if
                     probe=False, e.g. for non-HTTP backends like Slurm CLI).
    """
    url = _get_env_with_secrets(env_var)
    if not url:
        pytest.skip(f"{env_var} not set — set it to run this provider E2E")
    if probe:
        ok, why = _http_alive(url, path)
        if not ok:
            pytest.skip(
                f"backend at {url!r} ({env_var}) not reachable: {why}"
            )
    return url  # type: ignore[return-value]  # pytest.skip() raises, so we always return str


def skip_unless(provider: str) -> None:
    """Skip the current test unless the named provider backend is configured + reachable.

    Supported provider names: 'ollama', 'vllm', 'llamacpp', 'azure'.
    For slurm use require_slurm() which has its own liveness probe.

    Raises pytest.skip.Exception when not configured/reachable.
    """
    env_var = BACKEND_ENV_VARS.get(provider)
    if env_var is None:
        raise ValueError(f"Unknown provider {provider!r}. Known: {sorted(BACKEND_ENV_VARS)}")
    require_backend(env_var)


def require_slurm_cli() -> None:
    """Skip unless SLURM_E2E=1 and sbatch is available on PATH."""
    import shutil

    if os.environ.get("SLURM_E2E") != "1":
        pytest.skip("SLURM_E2E not set to '1' — set it to run slurm CLI E2E (submits real jobs)")
    if shutil.which("sbatch") is None:
        pytest.skip("sbatch not found on PATH — slurm CLI not available")


def require_slurm_rest() -> str:
    """Skip unless SLURM_REST_URL is set and the REST API /ping responds.

    Returns the base REST URL.
    """
    url = _get_env_with_secrets("SLURM_REST_URL")
    if not url:
        pytest.skip("SLURM_REST_URL not set — set it to run slurm REST E2E")
    # Slurm REST ping path
    ok, why = _http_alive(url, "/slurm/v0.0.40/ping", timeout=3.0)
    if not ok:
        pytest.skip(f"Slurm REST API at {url!r} not reachable: {why}")
    return url  # type: ignore[return-value]


def require_azure_provision() -> None:
    """Skip unless AZURE_PROVISION_E2E=1 and required env vars are set."""
    if os.environ.get("AZURE_PROVISION_E2E") != "1":
        pytest.skip(
            "AZURE_PROVISION_E2E not set to '1' — this test provisions real "
            "Azure GPU VMs and incurs cost; run it manually/scheduled only"
        )
    for var in ("AZURE_SUBSCRIPTION_ID", "AZURE_RESOURCE_GROUP", "GLUDD_E2E_MAX_SPEND_USD"):
        if not _get_env_with_secrets(var):
            pytest.skip(f"{var} not set — required for azure provision E2E")
