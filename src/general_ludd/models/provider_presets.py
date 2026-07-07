"""Provider presets — hardcoded API URLs, packages, and credential mappings.

These are the source of truth for provider configuration. When a user selects
a provider and provides an API key, the system auto-configures everything else.
"""

from __future__ import annotations

PROVIDER_PRESETS: dict[str, dict[str, object]] = {
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
        "api_base_url": "https://api.z.ai/api/coding/paas/v4",
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
    "baseten": {
        "api_base_url": "https://inference.baseten.co/v1",
        "provider_package": "langchain-openai",
        "provider_class": "ChatOpenAI",
        "credential_env_var": "BASETEN_API_KEY",
        "credential_alias": "baseten_api_key",
        "api_base_alias": "baseten_api_base",
        "display_name": "Baseten",
        "free_models_endpoint": None,
        "supports_free_models": False,
    },
    "lambdalabs": {
        "api_base_url": "https://api.lambdalabs.ai/v1",
        "provider_package": "langchain-openai",
        "provider_class": "ChatOpenAI",
        "credential_env_var": "LAMBDALABS_API_KEY",
        "credential_alias": "lambdalabs_api_key",
        "api_base_alias": "lambdalabs_api_base",
        "display_name": "Lambda Labs",
        "free_models_endpoint": None,
        "supports_free_models": False,
    },
    "together": {
        "api_base_url": "https://api.together.xyz/v1",
        "provider_package": "langchain-openai",
        "provider_class": "ChatOpenAI",
        "credential_env_var": "TOGETHER_API_KEY",
        "credential_alias": "together_api_key",
        "api_base_alias": "together_api_base",
        "display_name": "Together AI",
        "free_models_endpoint": "https://api.together.xyz/v1/models",
        "supports_free_models": True,
    },
    "fireworks": {
        "api_base_url": "https://api.fireworks.ai/inference/v1",
        "provider_package": "langchain-openai",
        "provider_class": "ChatOpenAI",
        "credential_env_var": "FIREWORKS_API_KEY",
        "credential_alias": "fireworks_api_key",
        "api_base_alias": "fireworks_api_base",
        "display_name": "Fireworks AI",
        "free_models_endpoint": None,
        "supports_free_models": False,
    },
    "replicate": {
        "api_base_url": "https://api.replicate.com/v1/openai",
        "provider_package": "langchain-openai",
        "provider_class": "ChatOpenAI",
        "credential_env_var": "REPLICATE_API_TOKEN",
        "credential_alias": "replicate_api_key",
        "api_base_alias": "replicate_api_base",
        "display_name": "Replicate",
        "free_models_endpoint": None,
        "supports_free_models": False,
    },
    "runpod": {
        "api_base_url": "https://api.runpod.ai/v2/openai/v1",
        "provider_package": "langchain-openai",
        "provider_class": "ChatOpenAI",
        "credential_env_var": "RUNPOD_API_KEY",
        "credential_alias": "runpod_api_key",
        "api_base_alias": "runpod_api_base",
        "display_name": "RunPod",
        "free_models_endpoint": None,
        "supports_free_models": False,
    },
    "modal": {
        "api_base_url": "https://modal.com/v1/openai",
        "provider_package": "langchain-openai",
        "provider_class": "ChatOpenAI",
        "credential_env_var": "MODAL_API_TOKEN",
        "credential_alias": "modal_api_key",
        "api_base_alias": "modal_api_base",
        "display_name": "Modal",
        "free_models_endpoint": None,
        "supports_free_models": False,
    },
    "coreweave": {
        "api_base_url": "https://api.coreweave.cloud/v1/openai",
        "provider_package": "langchain-openai",
        "provider_class": "ChatOpenAI",
        "credential_env_var": "COREWEAVE_API_KEY",
        "credential_alias": "coreweave_api_key",
        "api_base_alias": "coreweave_api_base",
        "display_name": "CoreWeave",
        "free_models_endpoint": None,
        "supports_free_models": False,
    },
    "mistral": {
        "api_base_url": "https://api.mistral.ai/v1",
        "provider_package": "langchain-openai",
        "provider_class": "ChatOpenAI",
        "credential_env_var": "MISTRAL_API_KEY",
        "credential_alias": "mistral_api_key",
        "api_base_alias": "mistral_api_base",
        "display_name": "Mistral AI",
        "free_models_endpoint": None,
        "supports_free_models": False,
    },
    "cohere": {
        "api_base_url": "https://api.cohere.ai/compatibility/v1",
        "provider_package": "langchain-openai",
        "provider_class": "ChatOpenAI",
        "credential_env_var": "CO_API_KEY",
        "credential_alias": "cohere_api_key",
        "api_base_alias": "cohere_api_base",
        "display_name": "Cohere",
        "free_models_endpoint": None,
        "supports_free_models": False,
    },
    "nvidia": {
        "api_base_url": "https://integrate.api.nvidia.com/v1",
        "provider_package": "langchain-openai",
        "provider_class": "ChatOpenAI",
        "credential_env_var": "NVIDIA_API_KEY",
        "credential_alias": "nvidia_api_key",
        "api_base_alias": "nvidia_api_base",
        "display_name": "NVIDIA NIM",
        "free_models_endpoint": None,
        "supports_free_models": False,
    },
    "perplexity": {
        "api_base_url": "https://api.perplexity.ai/chat/completions",
        "provider_package": "langchain-openai",
        "provider_class": "ChatOpenAI",
        "credential_env_var": "PERPLEXITY_API_KEY",
        "credential_alias": "perplexity_api_key",
        "api_base_alias": "perplexity_api_base",
        "display_name": "Perplexity",
        "free_models_endpoint": None,
        "supports_free_models": False,
    },
    "huggingface": {
        "api_base_url": "https://api-inference.huggingface.co/models",
        "provider_package": "langchain-huggingface",
        "provider_class": "HuggingFaceEndpoint",
        "credential_env_var": "HF_TOKEN",
        "credential_alias": "huggingface_api_key",
        "api_base_alias": "huggingface_api_base",
        "display_name": "Hugging Face",
        "free_models_endpoint": None,
        "supports_free_models": False,
    },
    "ai21": {
        "api_base_url": "https://api.ai21.com/studio/v1",
        "provider_package": "langchain-openai",
        "provider_class": "ChatOpenAI",
        "credential_env_var": "AI21_API_KEY",
        "credential_alias": "ai21_api_key",
        "api_base_alias": "ai21_api_base",
        "display_name": "AI21",
        "free_models_endpoint": None,
        "supports_free_models": False,
    },
    "google": {
        "api_base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "provider_package": "langchain-openai",
        "provider_class": "ChatOpenAI",
        "credential_env_var": "GOOGLE_API_KEY",
        "credential_alias": "google_api_key",
        "api_base_alias": "google_api_base",
        "display_name": "Google",
        "free_models_endpoint": None,
        "supports_free_models": False,
    },
}


PROVIDER_FLAGSHIP_MODELS: dict[str, str] = {
    "openrouter": "anthropic/claude-3.5-sonnet",
    "openai": "gpt-4o",
    "anthropic": "claude-3-5-sonnet-20241022",
    "zai": "glm-4.5",
    "groq": "llama-3.3-70b-versatile",
    "deepseek": "deepseek-chat",
    "baseten": "meta-llama/Llama-3.1-70B-Instruct",
    "lambdalabs": "meta-llama/Llama-3.1-70B-Instruct",
    "together": "meta-llama/Llama-3.1-70B-Instruct",
    "fireworks": "meta-llama/Llama-3.1-70B-Instruct",
    "replicate": "meta-llama/Meta-Llama-3.1-70B-Instruct",
    "runpod": "meta-llama/Llama-3.1-70B-Instruct",
    "modal": "meta-llama/Llama-3.1-70B-Instruct",
    "coreweave": "meta-llama/Llama-3.1-70B-Instruct",
    "mistral": "mistral-large-latest",
    "cohere": "command-r-plus",
    "nvidia": "meta-llama/Llama-3.1-70B-Instruct",
    "perplexity": "llama-3.1-sonar-large-128k-online",
    "huggingface": "meta-llama/Llama-3.1-70B-Instruct",
    "ai21": "jamba-1.5-large",
    "google": "gemini-2.5-pro",
}


def get_provider_preset(provider_name: str) -> dict[str, object] | None:
    """Get the preset configuration for a provider. Returns None if unknown."""
    return PROVIDER_PRESETS.get(provider_name.lower())


def get_provider_flagship_model(provider_name: str) -> str | None:
    """Return the flagship model id for a provider, or None if unknown.

    Used by AutoConfigurator.auto_configure_from_env() to pick a sensible
    default model when an operator sets a provider credential env var
    (e.g. MISTRAL_API_KEY) without writing a full ModelProfile for it.
    """
    return PROVIDER_FLAGSHIP_MODELS.get(provider_name.lower())


def detect_credential_alias(provider_name: str, environ: dict[str, str] | None = None) -> bool:
    """Check if the credential env var for a provider is set in the environment."""
    import os

    preset = get_provider_preset(provider_name)
    if preset is None:
        return False
    env = environ if environ is not None else dict(os.environ)
    return preset["credential_env_var"] in env and bool(env[preset["credential_env_var"]])


def list_configured_providers(environ: dict[str, str] | None = None) -> list[str]:
    """List all providers that have credentials configured."""
    import os

    env = environ if environ is not None else dict(os.environ)
    configured: list[str] = []
    for name, preset in PROVIDER_PRESETS.items():
        if preset["credential_env_var"] in env and bool(env[preset["credential_env_var"]]):
            configured.append(name)
    return configured
