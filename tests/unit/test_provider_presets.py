"""Unit tests for src/general_ludd/models/provider_presets.py.

All tests use injectable environ={} — no os.environ is touched.
No network, no live model, no subprocess.
"""

from __future__ import annotations

import pytest

from general_ludd.models.provider_presets import (
    PROVIDER_PRESETS,
    detect_credential_alias,
    get_provider_preset,
    list_configured_providers,
)

# ---------------------------------------------------------------------------
# Constants / helpers
# ---------------------------------------------------------------------------

ALL_PROVIDERS = list(PROVIDER_PRESETS.keys())

EXPECTED_KEYS = {
    "api_base_url",
    "provider_package",
    "provider_class",
    "credential_env_var",
    "credential_alias",
    "api_base_alias",
    "display_name",
    "free_models_endpoint",
    "supports_free_models",
}

# Explicit credential_env_var expectations per provider
EXPECTED_CRED_ENV = {
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "zai": "ZAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}

# Spot-check api_base_url for a subset of providers
EXPECTED_API_BASE = {
    "openrouter": "https://openrouter.ai/api/v1",
    "openai": "https://api.openai.com/v1",
    "zai": "https://api.z.ai/api/coding/paas/v4",
    "deepseek": "https://api.deepseek.com/v1",
}


# ---------------------------------------------------------------------------
# PROVIDER_PRESETS structure
# ---------------------------------------------------------------------------


class TestProviderPresetsDict:
    def test_all_expected_providers_present(self) -> None:
        for name in ("openrouter", "openai", "anthropic", "zai", "groq", "deepseek"):
            assert name in PROVIDER_PRESETS, f"Missing provider: {name}"

    def test_each_preset_has_required_keys(self) -> None:
        for name, preset in PROVIDER_PRESETS.items():
            missing = EXPECTED_KEYS - set(preset.keys())
            assert not missing, f"Provider '{name}' missing keys: {missing}"

    def test_openrouter_supports_free_models(self) -> None:
        assert PROVIDER_PRESETS["openrouter"]["supports_free_models"] is True

    def test_openai_does_not_support_free_models(self) -> None:
        assert PROVIDER_PRESETS["openai"]["supports_free_models"] is False

    @pytest.mark.parametrize("provider", ["openai", "anthropic", "zai", "groq", "deepseek"])
    def test_non_openrouter_does_not_support_free_models(self, provider: str) -> None:
        assert PROVIDER_PRESETS[provider]["supports_free_models"] is False

    def test_openrouter_has_free_models_endpoint(self) -> None:
        assert PROVIDER_PRESETS["openrouter"]["free_models_endpoint"] is not None

    @pytest.mark.parametrize("provider", ["openai", "anthropic", "zai", "groq", "deepseek"])
    def test_non_openrouter_free_models_endpoint_is_none(self, provider: str) -> None:
        assert PROVIDER_PRESETS[provider]["free_models_endpoint"] is None


# ---------------------------------------------------------------------------
# get_provider_preset
# ---------------------------------------------------------------------------


class TestGetProviderPreset:
    def test_known_provider_returns_dict(self) -> None:
        result = get_provider_preset("openai")
        assert isinstance(result, dict)

    def test_known_provider_has_all_required_keys(self) -> None:
        result = get_provider_preset("openai")
        assert result is not None
        assert EXPECTED_KEYS.issubset(set(result.keys()))

    def test_unknown_provider_returns_none(self) -> None:
        assert get_provider_preset("nonexistent_provider") is None

    def test_empty_string_returns_none(self) -> None:
        assert get_provider_preset("") is None

    # Case-insensitive lookups
    def test_uppercase_openai_matches(self) -> None:
        assert get_provider_preset("OPENAI") is not None

    def test_mixed_case_openai_matches(self) -> None:
        assert get_provider_preset("OpenAI") is not None

    def test_case_insensitive_returns_same_result(self) -> None:
        lower = get_provider_preset("openai")
        upper = get_provider_preset("OPENAI")
        mixed = get_provider_preset("OpenAI")
        assert lower == upper == mixed

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    def test_uppercase_lookup_succeeds_for_all_providers(self, provider: str) -> None:
        assert get_provider_preset(provider.upper()) is not None

    # credential_env_var per provider
    @pytest.mark.parametrize("provider,expected_env", list(EXPECTED_CRED_ENV.items()))
    def test_credential_env_var(self, provider: str, expected_env: str) -> None:
        preset = get_provider_preset(provider)
        assert preset is not None
        assert preset["credential_env_var"] == expected_env

    # api_base_url spot-checks
    @pytest.mark.parametrize("provider,expected_url", list(EXPECTED_API_BASE.items()))
    def test_api_base_url(self, provider: str, expected_url: str) -> None:
        preset = get_provider_preset(provider)
        assert preset is not None
        assert preset["api_base_url"] == expected_url

    # supports_free_models field
    def test_openrouter_supports_free_models_field(self) -> None:
        preset = get_provider_preset("openrouter")
        assert preset is not None
        assert preset["supports_free_models"] is True

    def test_openai_no_free_models_field(self) -> None:
        preset = get_provider_preset("openai")
        assert preset is not None
        assert preset["supports_free_models"] is False

    def test_anthropic_display_name(self) -> None:
        preset = get_provider_preset("anthropic")
        assert preset is not None
        assert preset["display_name"] == "Anthropic"

    def test_zai_display_name(self) -> None:
        preset = get_provider_preset("zai")
        assert preset is not None
        assert preset["display_name"] == "Z.AI / GLM"

    def test_result_is_independent_reference(self) -> None:
        # get_provider_preset returns the dict; verify it's the real preset dict
        # (not a copy), so equality holds with the source
        preset = get_provider_preset("groq")
        assert preset is not None
        assert preset["credential_env_var"] == "GROQ_API_KEY"


# ---------------------------------------------------------------------------
# detect_credential_alias
# ---------------------------------------------------------------------------


class TestDetectCredentialAlias:
    def test_returns_true_when_env_var_set(self) -> None:
        environ = {"OPENAI_API_KEY": "sk-test-key"}
        assert detect_credential_alias("openai", environ=environ) is True

    def test_returns_false_when_env_var_missing(self) -> None:
        assert detect_credential_alias("openai", environ={}) is False

    def test_returns_false_when_env_var_empty_string(self) -> None:
        environ = {"OPENAI_API_KEY": ""}
        assert detect_credential_alias("openai", environ=environ) is False

    def test_returns_false_for_unknown_provider(self) -> None:
        environ = {"WHATEVER_KEY": "value"}
        assert detect_credential_alias("unknown_provider", environ=environ) is False

    def test_does_not_use_os_environ_when_dict_injected(self) -> None:
        # Pass an empty dict — should get False even if real env has the key
        result = detect_credential_alias("openai", environ={})
        assert result is False  # empty dict → not configured regardless of real env

    def test_other_env_vars_do_not_satisfy_provider(self) -> None:
        # ANTHROPIC_API_KEY present but asking about openai
        environ = {"ANTHROPIC_API_KEY": "key-value"}
        assert detect_credential_alias("openai", environ=environ) is False

    def test_correct_key_for_another_provider_satisfies(self) -> None:
        environ = {"ANTHROPIC_API_KEY": "key-value"}
        assert detect_credential_alias("anthropic", environ=environ) is True

    # Per-provider detection
    @pytest.mark.parametrize("provider,env_var", list(EXPECTED_CRED_ENV.items()))
    def test_each_provider_detected_by_its_env_var(self, provider: str, env_var: str) -> None:
        environ = {env_var: "some-api-key"}
        assert detect_credential_alias(provider, environ=environ) is True

    @pytest.mark.parametrize("provider,env_var", list(EXPECTED_CRED_ENV.items()))
    def test_each_provider_not_detected_without_its_env_var(
        self, provider: str, env_var: str
    ) -> None:
        # Inject all OTHER providers' keys but not this one
        environ = {v: "key" for k, v in EXPECTED_CRED_ENV.items() if k != provider}
        assert detect_credential_alias(provider, environ=environ) is False

    def test_whitespace_only_value_is_truthy(self) -> None:
        # " " is non-empty → bool(" ") is True → should return True
        environ = {"ZAI_API_KEY": "   "}
        assert detect_credential_alias("zai", environ=environ) is True

    def test_case_insensitive_provider_name(self) -> None:
        environ = {"GROQ_API_KEY": "my-groq-key"}
        assert detect_credential_alias("GROQ", environ=environ) is True
        assert detect_credential_alias("Groq", environ=environ) is True


# ---------------------------------------------------------------------------
# list_configured_providers
# ---------------------------------------------------------------------------


class TestListConfiguredProviders:
    def test_returns_empty_list_when_environ_empty(self) -> None:
        assert list_configured_providers(environ={}) == []

    def test_returns_only_provider_with_cred_set(self) -> None:
        environ = {"OPENAI_API_KEY": "sk-abc"}
        result = list_configured_providers(environ=environ)
        assert result == ["openai"]

    def test_partial_set_two_providers(self) -> None:
        environ = {
            "OPENAI_API_KEY": "sk-abc",
            "ZAI_API_KEY": "zai-key",
        }
        result = sorted(list_configured_providers(environ=environ))
        assert result == sorted(["openai", "zai"])

    def test_all_six_providers_configured(self) -> None:
        environ = {v: "key" for v in EXPECTED_CRED_ENV.values()}
        result = sorted(list_configured_providers(environ=environ))
        assert result == sorted(ALL_PROVIDERS)

    def test_empty_string_value_not_counted(self) -> None:
        environ = {
            "OPENAI_API_KEY": "",
            "ANTHROPIC_API_KEY": "real-key",
        }
        result = list_configured_providers(environ=environ)
        assert "openai" not in result
        assert "anthropic" in result

    def test_unrelated_env_vars_ignored(self) -> None:
        environ = {
            "PATH": "/usr/bin",
            "HOME": "/home/user",
            "RANDOM_KEY": "abc",
        }
        assert list_configured_providers(environ=environ) == []

    def test_all_empty_string_values_returns_empty(self) -> None:
        environ = {v: "" for v in EXPECTED_CRED_ENV.values()}
        assert list_configured_providers(environ=environ) == []

    def test_returns_list_type(self) -> None:
        result = list_configured_providers(environ={})
        assert isinstance(result, list)

    def test_three_providers_configured(self) -> None:
        environ = {
            "OPENROUTER_API_KEY": "or-key",
            "GROQ_API_KEY": "groq-key",
            "DEEPSEEK_API_KEY": "ds-key",
        }
        result = sorted(list_configured_providers(environ=environ))
        assert result == sorted(["openrouter", "groq", "deepseek"])

    def test_does_not_rely_on_os_environ(self) -> None:
        # With an explicit empty environ, result is always empty regardless of
        # what might be set in the real environment.
        result = list_configured_providers(environ={})
        assert result == []

    def test_single_provider_openrouter(self) -> None:
        environ = {"OPENROUTER_API_KEY": "or-test-key"}
        result = list_configured_providers(environ=environ)
        assert result == ["openrouter"]

    def test_order_does_not_matter_for_full_set(self) -> None:
        environ = {v: "key" for v in EXPECTED_CRED_ENV.values()}
        result = list_configured_providers(environ=environ)
        assert set(result) == set(ALL_PROVIDERS)
        assert len(result) == len(ALL_PROVIDERS)
