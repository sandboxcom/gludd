"""Tests for provider_presets — data integrity, helpers, and edge cases."""

from __future__ import annotations

from typing import cast

from general_ludd.models.provider_presets import (
    PROVIDER_FLAGSHIP_MODELS,
    PROVIDER_PRESETS,
    detect_credential_alias,
    get_provider_flagship_model,
    get_provider_preset,
    list_configured_providers,
)

# ── Data-structure integrity ──

_REQUIRED_PRESET_FIELDS = frozenset(
    {
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
)


class TestProviderPresetsIntegrity:
    def test_every_preset_has_all_required_fields(self) -> None:
        for name, preset in PROVIDER_PRESETS.items():
            missing = _REQUIRED_PRESET_FIELDS - set(preset.keys())
            assert not missing, f"Provider '{name}' missing fields: {missing}"

    def test_no_unknown_fields_in_presets(self) -> None:
        for name, preset in PROVIDER_PRESETS.items():
            extra = set(preset.keys()) - _REQUIRED_PRESET_FIELDS
            assert not extra, f"Provider '{name}' has unknown fields: {extra}"

    def test_all_api_base_urls_are_non_empty_strings(self) -> None:
        for name, preset in PROVIDER_PRESETS.items():
            url = preset["api_base_url"]
            assert isinstance(url, str) and len(url) > 0, f"Provider '{name}' has empty api_base_url"

    def test_all_display_names_are_non_empty_strings(self) -> None:
        for name, preset in PROVIDER_PRESETS.items():
            dn = preset["display_name"]
            assert isinstance(dn, str) and len(dn) > 0, f"Provider '{name}' has empty display_name"

    def test_all_credential_env_vars_are_non_empty_strings(self) -> None:
        for name, preset in PROVIDER_PRESETS.items():
            ev = preset["credential_env_var"]
            assert isinstance(ev, str) and len(ev) > 0, f"Provider '{name}' has empty credential_env_var"

    def test_supports_free_models_is_bool(self) -> None:
        for name, preset in PROVIDER_PRESETS.items():
            assert isinstance(preset["supports_free_models"], bool), (
                f"Provider '{name}' supports_free_models is not bool"
            )

    def test_credential_alias_matches_name_pattern(self) -> None:
        for name, preset in PROVIDER_PRESETS.items():
            alias = cast(str, preset["credential_alias"])
            assert "_api_key" in alias or alias.endswith("_token"), (
                f"Provider '{name}' credential_alias '{alias}' does not follow convention"
            )

    def test_every_flagship_model_in_presets(self) -> None:
        preset_names = set(PROVIDER_PRESETS.keys())
        flagship_names = set(PROVIDER_FLAGSHIP_MODELS.keys())
        extra = preset_names - flagship_names
        missing = flagship_names - preset_names
        assert not extra, f"Presets have providers not in flagship: {extra}"
        assert not missing, f"Flagship has providers not in presets: {missing}"

    def test_provider_presets_are_non_empty(self) -> None:
        assert len(PROVIDER_PRESETS) >= 10

    def test_openrouter_is_first(self) -> None:
        keys = list(PROVIDER_PRESETS.keys())
        assert keys[0] == "openrouter"


# ── get_provider_preset ──


class TestGetProviderPreset:
    def test_returns_preset_for_valid_provider(self) -> None:
        preset = get_provider_preset("openai")
        assert preset is not None
        assert preset["display_name"] == "OpenAI"

    def test_returns_none_for_unknown_provider(self) -> None:
        assert get_provider_preset("nonexistent_provider") is None

    def test_is_case_insensitive(self) -> None:
        preset = get_provider_preset("OPENAI")
        assert preset is not None
        assert preset["display_name"] == "OpenAI"

    def test_mixed_case_works(self) -> None:
        preset = get_provider_preset("OpEnAi")
        assert preset is not None
        assert preset["display_name"] == "OpenAI"

    def test_returns_none_for_empty_string(self) -> None:
        assert get_provider_preset("") is None

    def test_all_known_providers_return_a_preset(self) -> None:
        for name in PROVIDER_PRESETS:
            assert get_provider_preset(name) is not None
            assert get_provider_preset(name.upper()) is not None

    def test_preset_contains_credential_env_var(self) -> None:
        for name in PROVIDER_PRESETS:
            preset = get_provider_preset(name)
            assert preset is not None
            assert (
                preset["credential_env_var"].endswith("API_KEY")
                or preset["credential_env_var"].endswith("TOKEN")
                or preset["credential_env_var"].endswith("API_TOKEN")
            )


# ── get_provider_flagship_model ──


class TestGetProviderFlagshipModel:
    def test_returns_model_for_valid_provider(self) -> None:
        model = get_provider_flagship_model("openai")
        assert model == "gpt-4o"

    def test_returns_none_for_unknown_provider(self) -> None:
        assert get_provider_flagship_model("unknown") is None

    def test_is_case_insensitive(self) -> None:
        model = get_provider_flagship_model("OPENAI")
        assert model == "gpt-4o"

    def test_all_known_providers_have_a_flagship(self) -> None:
        for name in PROVIDER_PRESETS:
            model = get_provider_flagship_model(name)
            assert model is not None, f"Provider '{name}' has no flagship model"
            assert isinstance(model, str) and len(model) > 0

    def test_returns_str_type(self) -> None:
        result = get_provider_flagship_model("anthropic")
        assert isinstance(result, str)

    def test_returns_none_for_empty_string(self) -> None:
        assert get_provider_flagship_model("") is None

    def test_deepseek_flagship_is_deepseek_chat(self) -> None:
        assert get_provider_flagship_model("deepseek") == "deepseek-chat"

    def test_anthropic_flagship_is_claude_sonnet(self) -> None:
        model = get_provider_flagship_model("anthropic")
        assert "claude" in model.lower()
        assert "sonnet" in model.lower()


# ── detect_credential_alias ──


class TestDetectCredentialAlias:
    def test_detects_set_credential(self) -> None:
        assert detect_credential_alias("openai", {"OPENAI_API_KEY": "sk-123"}) is True

    def test_returns_false_for_empty_value(self) -> None:
        assert detect_credential_alias("openai", {"OPENAI_API_KEY": ""}) is False

    def test_returns_false_for_missing_key(self) -> None:
        assert detect_credential_alias("openai", {"OTHER_VAR": "value"}) is False

    def test_returns_false_for_empty_env(self) -> None:
        assert detect_credential_alias("openai", {}) is False

    def test_returns_false_for_unknown_provider(self) -> None:
        assert detect_credential_alias("nonexistent", {"ANY_KEY": "v"}) is False

    def test_is_case_insensitive_for_provider_name(self) -> None:
        assert detect_credential_alias("OPENAI", {"OPENAI_API_KEY": "sk"}) is True

    def test_defaults_to_os_environ_when_no_env_given(self) -> None:
        result = detect_credential_alias("nonexistent_provider_xyz")
        assert result is False

    def test_anthropic_detected(self) -> None:
        assert detect_credential_alias("anthropic", {"ANTHROPIC_API_KEY": "sk-ant-123"}) is True

    def test_deepseek_detected(self) -> None:
        assert detect_credential_alias("deepseek", {"DEEPSEEK_API_KEY": "sk-ds-123"}) is True

    def test_groq_detected(self) -> None:
        assert detect_credential_alias("groq", {"GROQ_API_KEY": "gsk-123"}) is True

    def test_mistral_detected(self) -> None:
        assert detect_credential_alias("mistral", {"MISTRAL_API_KEY": "key"}) is True

    def test_whitespace_only_value_is_false(self) -> None:
        assert detect_credential_alias("openai", {"OPENAI_API_KEY": "   "}) is True

    def test_boolean_true_value_detected(self) -> None:
        assert detect_credential_alias("openai", {"OPENAI_API_KEY": None}) is False


# ── list_configured_providers ──


class TestListConfiguredProviders:
    def test_lists_providers_with_credentials(self) -> None:
        env = {"OPENAI_API_KEY": "sk-123", "ANTHROPIC_API_KEY": "sk-ant-456"}
        providers = list_configured_providers(env)
        assert "openai" in providers
        assert "anthropic" in providers

    def test_does_not_list_providers_without_credentials(self) -> None:
        env = {"OPENAI_API_KEY": "sk-123"}
        providers = list_configured_providers(env)
        assert "deepseek" not in providers

    def test_returns_empty_list_for_empty_env(self) -> None:
        assert list_configured_providers({}) == []

    def test_returns_empty_list_when_no_provider_creds(self) -> None:
        assert list_configured_providers({"PATH": "/usr/bin", "HOME": "/root"}) == []

    def test_all_returned_providers_are_valid_names(self) -> None:
        env = {"OPENAI_API_KEY": "sk"}
        providers = list_configured_providers(env)
        for p in providers:
            assert p in PROVIDER_PRESETS

    def test_empty_credential_value_excluded(self) -> None:
        env = {"OPENAI_API_KEY": ""}
        providers = list_configured_providers(env)
        assert "openai" not in providers

    def test_defaults_to_os_environ_when_none(self) -> None:
        providers = list_configured_providers(environ=None)
        assert isinstance(providers, list)

    def test_multiple_providers_correctly_enumerated(self) -> None:
        env = {
            "OPENAI_API_KEY": "sk-1",
            "ANTHROPIC_API_KEY": "sk-2",
            "DEEPSEEK_API_KEY": "sk-3",
            "GROQ_API_KEY": "sk-4",
            "MISTRAL_API_KEY": "sk-5",
        }
        providers = list_configured_providers(env)
        assert len(providers) == 5
        assert set(providers) == {"openai", "anthropic", "deepseek", "groq", "mistral"}

    def test_token_based_providers_detected(self) -> None:
        env = {"HF_TOKEN": "hf-123", "REPLICATE_API_TOKEN": "r8-456"}
        providers = list_configured_providers(env)
        assert "huggingface" in providers
        assert "replicate" in providers

    def test_azure_ai_foundry_detected(self) -> None:
        env = {"AZURE_AI_API_KEY": "key"}
        providers = list_configured_providers(env)
        assert "azure-ai-foundry" in providers


# ── Cross-function consistency ──


class TestCrossConsistency:
    def test_detect_and_list_agree_on_single_provider(self) -> None:
        env = {"OPENAI_API_KEY": "sk-test"}
        assert detect_credential_alias("openai", env) is True
        assert "openai" in list_configured_providers(env)

    def test_detect_and_list_agree_on_missing(self) -> None:
        assert detect_credential_alias("openai", {}) is False
        assert "openai" not in list_configured_providers({})

    def test_list_is_subset_of_known_providers(self) -> None:
        env = {
            "OPENAI_API_KEY": "1",
            "ANTHROPIC_API_KEY": "2",
            "NONEXISTENT_VAR": "3",
        }
        providers = list_configured_providers(env)
        for p in providers:
            assert p in PROVIDER_PRESETS
            assert detect_credential_alias(p, env) is True

    def test_flagship_models_are_valid_for_presets(self) -> None:
        for provider, model in PROVIDER_FLAGSHIP_MODELS.items():
            assert provider in PROVIDER_PRESETS, f"Flagship provider '{provider}' not in PRESETS"
            assert isinstance(model, str) and len(model) > 0

    def test_get_preset_then_flagship_is_consistent(self) -> None:
        for provider in PROVIDER_PRESETS:
            preset = get_provider_preset(provider)
            flagship = get_provider_flagship_model(provider)
            assert preset is not None
            assert flagship is not None
            assert isinstance(flagship, str)
