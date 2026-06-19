"""Tests for the ollama provider preset and is_ollama_available helper."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

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
        assert preset["credential_env_var"] is None

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


class TestIsOllamaAvailable:
    def test_not_configured_returns_false(self) -> None:
        """Empty env — no intent signal — returns False without probing."""
        assert is_ollama_available({}) is False

    def test_unrelated_env_returns_false(self) -> None:
        """Unrelated keys don't count as intent signal."""
        env = {"ZAI_API_KEY": "somekey", "OPENAI_API_KEY": "other"}  # pragma: allowlist secret
        assert is_ollama_available(env) is False

    def test_configured_and_up_returns_true(self) -> None:
        """OLLAMA_BASE_URL set + 2xx from /api/tags → True."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        env = {"OLLAMA_BASE_URL": "http://localhost:11434"}
        with patch("urllib.request.urlopen", return_value=mock_resp):
            assert is_ollama_available(env) is True

    def test_configured_and_down_url_error_returns_false(self) -> None:
        """OLLAMA_BASE_URL set but URLError (server down) → False."""
        import urllib.error
        env = {"OLLAMA_BASE_URL": "http://localhost:11434"}
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            assert is_ollama_available(env) is False

    def test_configured_and_timeout_returns_false(self) -> None:
        """OLLAMA_BASE_URL set but timeout → False."""
        env = {"OLLAMA_BASE_URL": "http://localhost:11434"}
        with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            assert is_ollama_available(env) is False

    def test_configured_via_model_and_up_returns_true(self) -> None:
        """OLLAMA_MODEL set (no BASE_URL) + 2xx → True, probes localhost:11434."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        env = {"OLLAMA_MODEL": "llama3"}
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            result = is_ollama_available(env)
        assert result is True
        called_url = mock_open.call_args[0][0]
        assert "localhost:11434" in called_url
        assert "/api/tags" in called_url

    def test_base_url_v1_suffix_stripped(self) -> None:
        """OLLAMA_BASE_URL ending in /v1 is stripped before probing /api/tags."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        env = {"OLLAMA_BASE_URL": "http://myhost:11434/v1"}
        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_open:
            is_ollama_available(env)
        called_url = mock_open.call_args[0][0]
        assert called_url == "http://myhost:11434/api/tags"

    def test_non_2xx_returns_false(self) -> None:
        """500 response → False."""
        mock_resp = MagicMock()
        mock_resp.status = 500
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        env = {"OLLAMA_BASE_URL": "http://localhost:11434"}
        with patch("urllib.request.urlopen", return_value=mock_resp):
            assert is_ollama_available(env) is False


class TestOllamaInListConfiguredProviders:
    def test_ollama_included_when_base_url_set_and_up(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        env = {"OLLAMA_BASE_URL": "http://localhost:11434"}
        with patch("urllib.request.urlopen", return_value=mock_resp):
            providers = list_configured_providers(env)
        assert "ollama" in providers

    def test_ollama_excluded_when_no_signal(self) -> None:
        providers = list_configured_providers({})
        assert "ollama" not in providers

    def test_other_providers_still_work(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        env = {"ZAI_API_KEY": "somekey", "OLLAMA_BASE_URL": "http://localhost:11434"}  # pragma: allowlist secret
        with patch("urllib.request.urlopen", return_value=mock_resp):
            providers = list_configured_providers(env)
        assert "zai" in providers
        assert "ollama" in providers


class TestDetectCredentialAliasOllama:
    def test_detect_without_api_key_returns_false(self) -> None:
        """credential_env_var is None for ollama → always False."""
        env = {"OLLAMA_BASE_URL": "http://localhost:11434"}
        assert detect_credential_alias("ollama", env) is False

    def test_detect_empty_env_returns_false(self) -> None:
        assert detect_credential_alias("ollama", {}) is False
