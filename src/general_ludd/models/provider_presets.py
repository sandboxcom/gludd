"""Provider presets — hardcoded API URLs, packages, and credential mappings.

These are the source of truth for provider configuration. When a user selects
a provider and provides an API key, the system auto-configures everything else.
"""

from __future__ import annotations

import urllib.request
from typing import Any

PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "openrouter": {
        "api_base_url": "https://openrouter.ai/api/v1",
        "provider_package": "langchain-openai",
        "provider_class": "ChatOpenAI",
        "credential_env_var": "OPENROUTER_API_KEY",
        "credential_alias": "openrouter_api_key",
        "api_base_alias": "openrouter_api_base",
        "display_name": "OpenRouter",
        "free_models_endpoint": "https://openrouter.ai/api/v1/models",
        "supports_free_models": True,
    },
    "openai": {
        "api_base_url": "https://api.openai.com/v1",
        "provider_package": "langchain-openai",
        "provider_class": "ChatOpenAI",
        "credential_env_var": "OPENAI_API_KEY",
        "credential_alias": "openai_api_key",
        "api_base_alias": "openai_api_base",
        "display_name": "OpenAI",
        "free_models_endpoint": None,
        "supports_free_models": False,
    },
    "anthropic": {
        "api_base_url": "https://api.anthropic.com/v1",
        "provider_package": "langchain-anthropic",
        "provider_class": "ChatAnthropic",
        "credential_env_var": "ANTHROPIC_API_KEY",
        "credential_alias": "anthropic_api_key",
        "api_base_alias": "anthropic_api_base",
        "display_name": "Anthropic",
        "free_models_endpoint": None,
        "supports_free_models": False,
    },
    "zai": {
        "api_base_url": "https://open.bigmodel.cn/api/paas/v4",
        "provider_package": "langchain-openai",
        "provider_class": "ChatOpenAI",
        "credential_env_var": "ZAI_API_KEY",
        "credential_alias": "zai_api_key",
        "api_base_alias": "zai_api_base",
        "display_name": "Z.AI / GLM",
        "free_models_endpoint": None,
        "supports_free_models": False,
    },
    "groq": {
        "api_base_url": "https://api.groq.com/openai/v1",
        "provider_package": "langchain-openai",
        "provider_class": "ChatOpenAI",
        "credential_env_var": "GROQ_API_KEY",
        "credential_alias": "groq_api_key",
        "api_base_alias": "groq_api_base",
        "display_name": "Groq",
        "free_models_endpoint": None,
        "supports_free_models": False,
    },
    "deepseek": {
        "api_base_url": "https://api.deepseek.com/v1",
        "provider_package": "langchain-openai",
        "provider_class": "ChatOpenAI",
        "credential_env_var": "DEEPSEEK_API_KEY",
        "credential_alias": "deepseek_api_key",
        "api_base_alias": "deepseek_api_base",
        "display_name": "DeepSeek",
        "free_models_endpoint": None,
        "supports_free_models": False,
    },
    "ollama": {
        "api_base_url": "http://localhost:11434/v1",
        "provider_package": "langchain-openai",
        "provider_class": "ChatOpenAI",
        "credential_env_var": None,
        "credential_alias": "ollama_api_key",
        "api_base_alias": "ollama_api_base",
        "display_name": "Ollama (local)",
        "free_models_endpoint": None,
        "supports_free_models": True,
    },
}


def get_provider_preset(provider_name: str) -> dict[str, Any] | None:
    """Get the preset configuration for a provider. Returns None if unknown."""
    return PROVIDER_PRESETS.get(provider_name.lower())


def detect_credential_alias(provider_name: str, environ: dict[str, str] | None = None) -> bool:
    """Check if the credential env var for a provider is set in the environment."""
    import os

    preset = get_provider_preset(provider_name)
    if preset is None:
        return False
    env = environ if environ is not None else dict(os.environ)
    cred_var = preset["credential_env_var"]
    if cred_var is None:
        return False
    return cred_var in env and bool(env[cred_var])


def list_configured_providers(environ: dict[str, str] | None = None) -> list[str]:
    """List all providers that have credentials configured."""
    import os

    env = environ if environ is not None else dict(os.environ)
    configured: list[str] = []
    for name, preset in PROVIDER_PRESETS.items():
        if name == "ollama":
            if is_ollama_available(env):
                configured.append(name)
        else:
            cred_var = preset["credential_env_var"]
            if cred_var is not None and cred_var in env and bool(env[cred_var]):
                configured.append(name)
    return configured


def is_ollama_available(environ: dict[str, str] | None = None) -> bool:
    """Return True if Ollama is configured AND reachable via a live HTTP probe.

    Precondition: OLLAMA_BASE_URL or OLLAMA_MODEL must be set to signal intent.
    If the precondition passes, attempts a GET to {base_url}/api/tags with a
    2-second timeout using stdlib urllib only (no extra dependencies).
    Returns False on any error, timeout, or non-2xx response.
    """
    import os

    env = environ if environ is not None else dict(os.environ)
    # Precondition: at least one intent signal must be present
    if not env.get("OLLAMA_BASE_URL") and not env.get("OLLAMA_MODEL"):
        return False
    # Determine probe base URL: prefer explicit OLLAMA_BASE_URL
    probe_base = env.get("OLLAMA_BASE_URL", "http://localhost:11434")
    # Strip trailing /v1 suffix if present (Ollama's /api/tags is at root)
    probe_base = probe_base.rstrip("/")
    if probe_base.endswith("/v1"):
        probe_base = probe_base[:-3]
    url = f"{probe_base}/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            return bool(200 <= int(resp.status) < 300)
    except Exception:
        return False
