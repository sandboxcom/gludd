"""Tests for the ollama provider preset and is_ollama_available helper."""
from __future__ import annotations

from general_ludd.models.provider_presets import (
    PROVIDER_PRESETS,
    detect_credential_alias,
    get_provider_preset,
    is_ollama_available,
    list_configured_providers,
)


class TestOllamaPresetShape:
    def test_ollama_preset_exists(self) -> None:
        assert "ollama" in PROVIDER_PRESETS

    def test_ollama_preset_api_base_url(self) -> None:
        preset = PROVIDER_PRESETS["ollama"]
        assert preset["api_base_url"] == "http://localhost:11434/v1"

    def test_ollama_preset_openai_compatible(self) -> None:
        preset = PROVIDER_PRESETS["ollama"]
        assert preset["provider_package"] == "langchain-openai"
        assert preset["provider_class"] == "ChatOpenAI"

    def test_ollama_preset_credential_env_var(self) -> None:
        preset = PROVIDER_PRESETS["ollama"]
        assert preset["credential_env_var"] == "OLLAMA_API_KEY"

    def test_ollama_preset_aliases(self) -> None:
        preset = PROVIDER_PRESETS["ollama"]
        assert preset["credential_alias"] == "ollama_api_key"
        assert preset["api_base_alias"] == "ollama_api_base"

    def test_ollama_preset_display_name(self) -> None:
        preset = PROVIDER_PRESETS["ollama"]
        assert "Ollama" in preset["display_name"]

    def test_ollama_preset_supports_free_models(self) -> None:
        preset = PROVIDER_PRESETS["ollama"]
        assert preset["supports_free_models"] is True

    def test_get_provider_preset_returns_ollama(self) -> None:
        preset = get_provider_preset("ollama")
        assert preset is not None
        assert preset["display_name"] == "Ollama (local)"

    def test_get_provider_preset_case_insensitive(self) -> None:
        assert get_provider_preset("OLLAMA") is not None
        assert get_provider_preset("Ollama") is not None

    def test_env_override_base_url(self) -> None:
        """OLLAMA_BASE_URL in env signals ollama availability."""
        env = {"OLLAMA_BASE_URL": "http://myhost:11434/v1"}
        assert is_ollama_available(env) is True

    def test_env_override_model(self) -> None:
        """OLLAMA_MODEL alone signals ollama availability."""
        env = {"OLLAMA_MODEL": "llama3"}
        assert is_ollama_available(env) is True

    def test_env_api_key(self) -> None:
        """OLLAMA_API_KEY (rare) also signals availability."""
        env = {"OLLAMA_API_KEY": "dummy"}  # pragma: allowlist secret
        assert is_ollama_available(env) is True

    def test_no_env_not_available(self) -> None:
        """Empty env → ollama not available."""
        assert is_ollama_available({}) is False

    def test_unrelated_env_not_available(self) -> None:
        """Unrelated keys don't count as ollama availability."""
        env = {"ZAI_API_KEY": "somekey", "OPENAI_API_KEY": "other"}  # pragma: allowlist secret
        assert is_ollama_available(env) is False


class TestOllamaInListConfiguredProviders:
    def test_ollama_included_when_base_url_set(self) -> None:
        env = {"OLLAMA_BASE_URL": "http://localhost:11434/v1"}
        providers = list_configured_providers(env)
        assert "ollama" in providers

    def test_ollama_included_when_model_set(self) -> None:
        env = {"OLLAMA_MODEL": "mistral"}
        providers = list_configured_providers(env)
        assert "ollama" in providers

    def test_ollama_excluded_when_no_signal(self) -> None:
        providers = list_configured_providers({})
        assert "ollama" not in providers

    def test_other_providers_still_work(self) -> None:
        env = {"ZAI_API_KEY": "somekey", "OLLAMA_BASE_URL": "http://localhost:11434/v1"}  # pragma: allowlist secret
        providers = list_configured_providers(env)
        assert "zai" in providers
        assert "ollama" in providers


class TestDetectCredentialAliasOllama:
    def test_detect_with_api_key_set(self) -> None:
        env = {"OLLAMA_API_KEY": "token"}  # pragma: allowlist secret
        assert detect_credential_alias("ollama", env) is True

    def test_detect_without_api_key(self) -> None:
        env = {"OLLAMA_BASE_URL": "http://localhost:11434/v1"}
        assert detect_credential_alias("ollama", env) is False
