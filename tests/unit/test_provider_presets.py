"""Tests for GPU/compute provider presets (baseten, lambdalabs, together, etc.)."""

from __future__ import annotations

import pytest

NEW_GPU_PROVIDERS = [
    "baseten",
    "lambdalabs",
    "together",
    "fireworks",
    "replicate",
    "runpod",
    "modal",
    "coreweave",
]

# Providers that must each have a flagship model entry in PROVIDER_FLAGSHIP_MODELS.
# Covers the original six plus every new GPU/LLM provider added in this change.
ALL_FLAGSHIP_PROVIDERS = [
    "openrouter",
    "openai",
    "anthropic",
    "zai",
    "groq",
    "deepseek",
    "baseten",
    "lambdalabs",
    "together",
    "fireworks",
    "replicate",
    "runpod",
    "modal",
    "coreweave",
    "mistral",
    "cohere",
    "nvidia",
    "perplexity",
    "huggingface",
    "google",
    "ai21",
]

# Pinned flagship model ids cited in the task spec — locks the contract so
# downstream auto-config emits the models users actually expect.
EXPECTED_FLAGSHIP_MODELS = {
    "mistral": "mistral-large-latest",
    "cohere": "command-r-plus",
    "together": "meta-llama/Llama-3.1-70B-Instruct",
    "fireworks": "meta-llama/Llama-3.1-70B-Instruct",
    "ai21": "jamba-1.5-large",
    "google": "gemini-2.5-pro",
}

REQUIRED_FIELDS = {
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

EXPECTED_URLS = {
    "baseten": "https://inference.baseten.co/v1",
    "lambdalabs": "https://api.lambdalabs.ai/v1",
    "together": "https://api.together.xyz/v1",
    "fireworks": "https://api.fireworks.ai/inference/v1",
    "replicate": "https://api.replicate.com/v1/openai",
    "runpod": "https://api.runpod.ai/v2/openai/v1",
    "modal": "https://modal.com/v1/openai",
    "coreweave": "https://api.coreweave.cloud/v1/openai",
}

EXPECTED_CREDENTIALS = {
    "baseten": "BASETEN_API_KEY",
    "lambdalabs": "LAMBDALABS_API_KEY",
    "together": "TOGETHER_API_KEY",
    "fireworks": "FIREWORKS_API_KEY",
    "replicate": "REPLICATE_API_TOKEN",
    "runpod": "RUNPOD_API_KEY",
    "modal": "MODAL_API_TOKEN",
    "coreweave": "COREWEAVE_API_KEY",
}

EXPECTED_DISPLAY = {
    "baseten": "Baseten",
    "lambdalabs": "Lambda Labs",
    "together": "Together AI",
    "fireworks": "Fireworks AI",
    "replicate": "Replicate",
    "runpod": "RunPod",
    "modal": "Modal",
    "coreweave": "CoreWeave",
}


class TestNewGpuProviderPresets:
    def test_all_new_providers_present(self):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        for name in NEW_GPU_PROVIDERS:
            assert name in PROVIDER_PRESETS, f"Provider '{name}' missing from PROVIDER_PRESETS"

    @pytest.mark.parametrize("provider", NEW_GPU_PROVIDERS)
    def test_provider_has_all_required_fields(self, provider):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        preset = PROVIDER_PRESETS[provider]
        missing = REQUIRED_FIELDS - set(preset.keys())
        assert not missing, f"Provider '{provider}' missing fields: {missing}"

    @pytest.mark.parametrize("provider", NEW_GPU_PROVIDERS)
    def test_provider_uses_openai_compatible_stack(self, provider):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        preset = PROVIDER_PRESETS[provider]
        assert preset["provider_package"] == "langchain-openai"
        assert preset["provider_class"] == "ChatOpenAI"

    @pytest.mark.parametrize("provider", NEW_GPU_PROVIDERS)
    def test_provider_api_base_url(self, provider):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        assert PROVIDER_PRESETS[provider]["api_base_url"] == EXPECTED_URLS[provider]

    @pytest.mark.parametrize("provider", NEW_GPU_PROVIDERS)
    def test_provider_credential_env_var(self, provider):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        assert PROVIDER_PRESETS[provider]["credential_env_var"] == EXPECTED_CREDENTIALS[provider]

    @pytest.mark.parametrize("provider", NEW_GPU_PROVIDERS)
    def test_provider_display_name(self, provider):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        assert PROVIDER_PRESETS[provider]["display_name"] == EXPECTED_DISPLAY[provider]

    def test_together_supports_free_models(self):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        preset = PROVIDER_PRESETS["together"]
        assert preset["supports_free_models"] is True
        assert preset["free_models_endpoint"] == "https://api.together.xyz/v1/models"

    @pytest.mark.parametrize("provider", [p for p in NEW_GPU_PROVIDERS if p != "together"])
    def test_non_together_does_not_advertise_free_models(self, provider):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        preset = PROVIDER_PRESETS[provider]
        assert preset["supports_free_models"] is False
        assert preset["free_models_endpoint"] is None


class TestGetProviderPresetForNewGpu:
    @pytest.mark.parametrize("provider", NEW_GPU_PROVIDERS)
    def test_get_returns_dict(self, provider):
        from general_ludd.models.provider_presets import get_provider_preset

        preset = get_provider_preset(provider)
        assert preset is not None
        assert isinstance(preset, dict)
        assert preset["api_base_url"] == EXPECTED_URLS[provider]

    def test_get_baseten_returns_correct_dict(self):
        from general_ludd.models.provider_presets import get_provider_preset

        preset = get_provider_preset("baseten")
        assert preset is not None
        assert preset["api_base_url"] == "https://inference.baseten.co/v1"
        assert preset["credential_env_var"] == "BASETEN_API_KEY"
        assert preset["display_name"] == "Baseten"
        assert preset["credential_alias"] == "baseten_api_key"
        assert preset["api_base_alias"] == "baseten_api_base"

    def test_get_is_case_insensitive(self):
        from general_ludd.models.provider_presets import get_provider_preset

        assert get_provider_preset("BASETEN") is not None
        assert get_provider_preset("LambdaLabs") is not None
        assert get_provider_preset("Together") is not None


class TestDetectCredentialAliasForNewGpu:
    @pytest.mark.parametrize("provider", NEW_GPU_PROVIDERS)
    def test_returns_true_when_credential_set(self, provider):
        from general_ludd.models.provider_presets import detect_credential_alias

        env_var = EXPECTED_CREDENTIALS[provider]
        assert detect_credential_alias(provider, {env_var: "sk-test"}) is True

    @pytest.mark.parametrize("provider", NEW_GPU_PROVIDERS)
    def test_returns_false_when_credential_missing(self, provider):
        from general_ludd.models.provider_presets import detect_credential_alias

        assert detect_credential_alias(provider, {}) is False

    @pytest.mark.parametrize("provider", NEW_GPU_PROVIDERS)
    def test_returns_false_when_credential_empty(self, provider):
        from general_ludd.models.provider_presets import detect_credential_alias

        env_var = EXPECTED_CREDENTIALS[provider]
        assert detect_credential_alias(provider, {env_var: ""}) is False

    @pytest.mark.parametrize("provider", NEW_GPU_PROVIDERS)
    def test_returns_false_unrelated_env(self, provider):
        from general_ludd.models.provider_presets import detect_credential_alias

        assert detect_credential_alias(provider, {"UNRELATED_VAR": "x"}) is False


class TestListConfiguredProvidersWithNewGpu:
    def test_lists_baseten_when_configured(self):
        from general_ludd.models.provider_presets import list_configured_providers

        configured = list_configured_providers({"BASETEN_API_KEY": "key"})
        assert "baseten" in configured

    def test_lists_multiple_new_gpu_providers(self):
        from general_ludd.models.provider_presets import list_configured_providers

        env = {
            "LAMBDALABS_API_KEY": "k1",
            "TOGETHER_API_KEY": "k2",
            "RUNPOD_API_KEY": "k3",
        }
        configured = list_configured_providers(env)
        assert "lambdalabs" in configured
        assert "together" in configured
        assert "runpod" in configured

    def test_new_gpu_mixed_with_legacy(self):
        from general_ludd.models.provider_presets import list_configured_providers

        env = {
            "OPENROUTER_API_KEY": "or",
            "MODAL_API_TOKEN": "modal",
            "COREWEAVE_API_KEY": "cw",
        }
        configured = list_configured_providers(env)
        assert "openrouter" in configured
        assert "modal" in configured
        assert "coreweave" in configured


class TestProviderFlagshipModels:
    """PROVIDER_FLAGSHIP_MODELS must cover every provider and pin the spec-named
    flagship models so AutoConfigurator emits sensible defaults."""

    def test_dict_exists_and_is_mapping(self):
        from general_ludd.models.provider_presets import PROVIDER_FLAGSHIP_MODELS

        assert isinstance(PROVIDER_FLAGSHIP_MODELS, dict)

    @pytest.mark.parametrize("provider", ALL_FLAGSHIP_PROVIDERS)
    def test_every_provider_has_flagship_entry(self, provider):
        from general_ludd.models.provider_presets import PROVIDER_FLAGSHIP_MODELS

        assert provider in PROVIDER_FLAGSHIP_MODELS, (
            f"Provider '{provider}' missing a flagship model entry in "
            f"PROVIDER_FLAGSHIP_MODELS"
        )

    @pytest.mark.parametrize("provider", ALL_FLAGSHIP_PROVIDERS)
    def test_flagship_model_is_nonempty_str(self, provider):
        from general_ludd.models.provider_presets import PROVIDER_FLAGSHIP_MODELS

        flagship = PROVIDER_FLAGSHIP_MODELS[provider]
        assert isinstance(flagship, str), f"{provider}: flagship must be str"
        assert flagship.strip(), f"{provider}: flagship must not be empty"

    def test_flagship_dict_covers_exactly_all_presets(self):
        """If a provider preset is added without a flagship entry, AutoConfigurator
        would silently fall back to the empty model_name — a bug. Lock 1:1 cover."""
        from general_ludd.models.provider_presets import (
            PROVIDER_FLAGSHIP_MODELS,
            PROVIDER_PRESETS,
        )

        missing = set(PROVIDER_PRESETS) - set(PROVIDER_FLAGSHIP_MODELS)
        assert not missing, (
            f"PROVIDER_FLAGSHIP_MODELS missing entries for: {sorted(missing)}"
        )

    @pytest.mark.parametrize("provider,expected", list(EXPECTED_FLAGSHIP_MODELS.items()))
    def test_spec_pinned_flagship_models(self, provider, expected):
        from general_ludd.models.provider_presets import PROVIDER_FLAGSHIP_MODELS

        assert PROVIDER_FLAGSHIP_MODELS[provider] == expected

    def test_get_helper_returns_flagship(self):
        from general_ludd.models.provider_presets import get_provider_flagship_model

        assert get_provider_flagship_model("mistral") == "mistral-large-latest"
        assert (
            get_provider_flagship_model("together")
            == "meta-llama/Llama-3.1-70B-Instruct"
        )

    def test_get_helper_unknown_provider_returns_none(self):
        from general_ludd.models.provider_presets import get_provider_flagship_model

        assert get_provider_flagship_model("does-not-exist") is None

    def test_get_helper_is_case_insensitive(self):
        from general_ludd.models.provider_presets import get_provider_flagship_model

        assert get_provider_flagship_model("MISTRAL") == "mistral-large-latest"
        assert get_provider_flagship_model("Cohere") == "command-r-plus"


class TestAutoConfigureFromEnv:
    """AutoConfigurator.auto_configure_from_env() must build one ModelProfile dict
    per provider whose credential env var is set, using the flagship model."""

    def test_returns_one_profile_per_configured_provider(self):
        from general_ludd.models.auto_configurator import AutoConfigurator

        env = {
            "FIREWORKS_API_KEY": "fw-key",
            "MISTRAL_API_KEY": "ms-key",
            "CO_API_KEY": "co-key",
        }
        configurator = AutoConfigurator()
        profiles = configurator.auto_configure_from_env(environ=env)

        providers = {p["provider"] for p in profiles}
        assert providers == {"fireworks", "mistral", "cohere"}

    def test_uses_flagship_model_for_each_profile(self):
        from general_ludd.models.auto_configurator import AutoConfigurator

        env = {
            "FIREWORKS_API_KEY": "fw-key",
            "MISTRAL_API_KEY": "ms-key",
            "CO_API_KEY": "co-key",
        }
        configurator = AutoConfigurator()
        profiles = configurator.auto_configure_from_env(environ=env)

        by_provider = {p["provider"]: p for p in profiles}
        assert (
            by_provider["fireworks"]["model_name"]
            == "meta-llama/Llama-3.1-70B-Instruct"
        )
        assert by_provider["mistral"]["model_name"] == "mistral-large-latest"
        assert by_provider["cohere"]["model_name"] == "command-r-plus"

    def test_skips_providers_without_credentials(self):
        from general_ludd.models.auto_configurator import AutoConfigurator

        configurator = AutoConfigurator()
        profiles = configurator.auto_configure_from_env(environ={})
        assert profiles == []

    def test_skips_empty_credential_values(self):
        from general_ludd.models.auto_configurator import AutoConfigurator

        env = {"MISTRAL_API_KEY": ""}
        configurator = AutoConfigurator()
        profiles = configurator.auto_configure_from_env(environ=env)
        assert profiles == []

    def test_profile_carries_preset_aliases(self):
        from general_ludd.models.auto_configurator import AutoConfigurator

        env = {"BASETEN_API_KEY": "b"}
        configurator = AutoConfigurator()
        profiles = configurator.auto_configure_from_env(environ=env)
        assert len(profiles) == 1
        p = profiles[0]
        assert p["provider"] == "baseten"
        assert p["api_base_alias"] == "baseten_api_base"
        assert p["credential_alias"] == "baseten_api_key"
        assert p["provider_package"] == "langchain-openai"
        assert p["provider_class_hint"] == "ChatOpenAI"

    def test_profile_id_includes_provider_and_model(self):
        from general_ludd.models.auto_configurator import AutoConfigurator

        env = {"MISTRAL_API_KEY": "k"}
        configurator = AutoConfigurator()
        profiles = configurator.auto_configure_from_env(environ=env)
        pid = profiles[0]["model_profile_id"]
        assert pid.startswith("mistral-")
        assert "mistral-large-latest".replace("/", "-") in pid

    def test_defaults_to_real_env_when_environ_omitted(self, monkeypatch):
        from general_ludd.models.auto_configurator import AutoConfigurator

        monkeypatch.setenv("MISTRAL_API_KEY", "real")
        monkeypatch.delenv("FIREWORKS_API_KEY", raising=False)
        monkeypatch.delenv("CO_API_KEY", raising=False)
        configurator = AutoConfigurator()
        profiles = configurator.auto_configure_from_env()
        providers = {p["provider"] for p in profiles}
        assert "mistral" in providers
        assert "fireworks" not in providers

    def test_generated_profiles_are_model_profile_constructible(self):
        """Profiles must be valid kwargs for ModelProfile (what daemon.py builds)."""
        from general_ludd.models.auto_configurator import AutoConfigurator
        from general_ludd.models.gateway import ModelProfile

        env = {
            "FIREWORKS_API_KEY": "k",
            "MISTRAL_API_KEY": "k2",
            "TOGETHER_API_KEY": "k3",
        }
        configurator = AutoConfigurator()
        profiles = configurator.auto_configure_from_env(environ=env)
        for p in profiles:
            mp = ModelProfile(**p)
            assert mp.enabled is True
            assert mp.model_name


class TestAutoConfigureModelProfileObjects:
    """The daemon and worker consume ModelProfile objects; auto_configure_profiles()
    must return them directly so the gateway construction site stays unchanged."""

    def test_returns_model_profile_objects(self):
        from general_ludd.models.auto_configurator import AutoConfigurator
        from general_ludd.models.gateway import ModelProfile

        env = {"MISTRAL_API_KEY": "k"}
        configurator = AutoConfigurator()
        profiles = configurator.auto_configure_profiles(environ=env)
        assert len(profiles) == 1
        assert isinstance(profiles[0], ModelProfile)
        assert profiles[0].provider == "mistral"
        assert profiles[0].model_name == "mistral-large-latest"


# --- New popular model providers (mistral, cohere, nvidia, perplexity, huggingface) ---

POPULAR_MODEL_PROVIDERS = [
    "mistral",
    "cohere",
    "nvidia",
    "perplexity",
    "huggingface",
    "ai21",
]

POPULAR_EXPECTED_URLS = {
    "mistral": "https://api.mistral.ai/v1",
    "cohere": "https://api.cohere.ai/compatibility/v1",
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "perplexity": "https://api.perplexity.ai/chat/completions",
    "huggingface": "https://api-inference.huggingface.co/models",
    "ai21": "https://api.ai21.com/studio/v1",
}

POPULAR_EXPECTED_CREDENTIALS = {
    "mistral": "MISTRAL_API_KEY",
    "cohere": "CO_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "perplexity": "PERPLEXITY_API_KEY",
    "huggingface": "HF_TOKEN",
    "ai21": "AI21_API_KEY",
}

POPULAR_EXPECTED_DISPLAY = {
    "mistral": "Mistral AI",
    "cohere": "Cohere",
    "nvidia": "NVIDIA NIM",
    "perplexity": "Perplexity",
    "huggingface": "Hugging Face",
    "ai21": "AI21",
}

OPENAI_COMPATIBLE_POPULAR = [
    "mistral",
    "cohere",
    "nvidia",
    "perplexity",
    "ai21",
]


class TestPopularModelProviderPresets:
    def test_all_popular_providers_present(self):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        for name in POPULAR_MODEL_PROVIDERS:
            assert name in PROVIDER_PRESETS, f"Provider '{name}' missing from PROVIDER_PRESETS"

    @pytest.mark.parametrize("provider", POPULAR_MODEL_PROVIDERS)
    def test_provider_has_all_required_fields(self, provider):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        preset = PROVIDER_PRESETS[provider]
        missing = REQUIRED_FIELDS - set(preset.keys())
        assert not missing, f"Provider '{provider}' missing fields: {missing}"

    @pytest.mark.parametrize("provider", POPULAR_MODEL_PROVIDERS)
    def test_provider_api_base_url(self, provider):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        assert PROVIDER_PRESETS[provider]["api_base_url"] == POPULAR_EXPECTED_URLS[provider]

    @pytest.mark.parametrize("provider", POPULAR_MODEL_PROVIDERS)
    def test_provider_credential_env_var(self, provider):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        assert PROVIDER_PRESETS[provider]["credential_env_var"] == POPULAR_EXPECTED_CREDENTIALS[provider]

    @pytest.mark.parametrize("provider", POPULAR_MODEL_PROVIDERS)
    def test_provider_display_name(self, provider):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        assert PROVIDER_PRESETS[provider]["display_name"] == POPULAR_EXPECTED_DISPLAY[provider]

    @pytest.mark.parametrize("provider", OPENAI_COMPATIBLE_POPULAR)
    def test_openai_compatible_stack(self, provider):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        preset = PROVIDER_PRESETS[provider]
        assert preset["provider_package"] == "langchain-openai"
        assert preset["provider_class"] == "ChatOpenAI"

    def test_huggingface_uses_dedicated_package(self):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        preset = PROVIDER_PRESETS["huggingface"]
        assert preset["provider_package"] == "langchain-huggingface"
        assert preset["provider_class"] == "HuggingFaceEndpoint"

    @pytest.mark.parametrize("provider", POPULAR_MODEL_PROVIDERS)
    def test_none_advertise_free_models(self, provider):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        preset = PROVIDER_PRESETS[provider]
        assert preset["supports_free_models"] is False
        assert preset["free_models_endpoint"] is None


class TestGetProviderPresetForPopularModel:
    @pytest.mark.parametrize("provider", POPULAR_MODEL_PROVIDERS)
    def test_get_returns_dict(self, provider):
        from general_ludd.models.provider_presets import get_provider_preset

        preset = get_provider_preset(provider)
        assert preset is not None
        assert isinstance(preset, dict)
        assert preset["api_base_url"] == POPULAR_EXPECTED_URLS[provider]

    def test_get_mistral_returns_correct_dict(self):
        from general_ludd.models.provider_presets import get_provider_preset

        preset = get_provider_preset("mistral")
        assert preset is not None
        assert preset["api_base_url"] == "https://api.mistral.ai/v1"
        assert preset["credential_env_var"] == "MISTRAL_API_KEY"
        assert preset["display_name"] == "Mistral AI"
        assert preset["credential_alias"] == "mistral_api_key"
        assert preset["api_base_alias"] == "mistral_api_base"

    def test_get_huggingface_returns_correct_dict(self):
        from general_ludd.models.provider_presets import get_provider_preset

        preset = get_provider_preset("huggingface")
        assert preset is not None
        assert preset["api_base_url"] == "https://api-inference.huggingface.co/models"
        assert preset["credential_env_var"] == "HF_TOKEN"
        assert preset["provider_package"] == "langchain-huggingface"
        assert preset["provider_class"] == "HuggingFaceEndpoint"

    def test_get_is_case_insensitive(self):
        from general_ludd.models.provider_presets import get_provider_preset

        assert get_provider_preset("MISTRAL") is not None
        assert get_provider_preset("Cohere") is not None
        assert get_provider_preset("NVIDIA") is not None
        assert get_provider_preset("Perplexity") is not None
        assert get_provider_preset("HuggingFace") is not None


class TestDetectCredentialAliasForPopularModel:
    @pytest.mark.parametrize("provider", POPULAR_MODEL_PROVIDERS)
    def test_returns_true_when_credential_set(self, provider):
        from general_ludd.models.provider_presets import detect_credential_alias

        env_var = POPULAR_EXPECTED_CREDENTIALS[provider]
        assert detect_credential_alias(provider, {env_var: "sk-test"}) is True

    @pytest.mark.parametrize("provider", POPULAR_MODEL_PROVIDERS)
    def test_returns_false_when_credential_missing(self, provider):
        from general_ludd.models.provider_presets import detect_credential_alias

        assert detect_credential_alias(provider, {}) is False

    @pytest.mark.parametrize("provider", POPULAR_MODEL_PROVIDERS)
    def test_returns_false_when_credential_empty(self, provider):
        from general_ludd.models.provider_presets import detect_credential_alias

        env_var = POPULAR_EXPECTED_CREDENTIALS[provider]
        assert detect_credential_alias(provider, {env_var: ""}) is False


class TestListConfiguredProvidersWithPopularModel:
    def test_lists_mistral_when_configured(self):
        from general_ludd.models.provider_presets import list_configured_providers

        configured = list_configured_providers({"MISTRAL_API_KEY": "key"})
        assert "mistral" in configured

    def test_lists_multiple_popular_providers(self):
        from general_ludd.models.provider_presets import list_configured_providers

        env = {
            "MISTRAL_API_KEY": "k1",
            "CO_API_KEY": "k2",
            "NVIDIA_API_KEY": "k3",
            "PERPLEXITY_API_KEY": "k4",
            "HF_TOKEN": "k5",
        }
        configured = list_configured_providers(env)
        assert "mistral" in configured
        assert "cohere" in configured
        assert "nvidia" in configured
        assert "perplexity" in configured
        assert "huggingface" in configured

    def test_popular_mixed_with_gpu_and_legacy(self):
        from general_ludd.models.provider_presets import list_configured_providers

        env = {
            "OPENAI_API_KEY": "oai",
            "TOGETHER_API_KEY": "tg",
            "MISTRAL_API_KEY": "mi",
            "HF_TOKEN": "hf",
        }
        configured = list_configured_providers(env)
        assert "openai" in configured
        assert "together" in configured
        assert "mistral" in configured
        assert "huggingface" in configured


# --- AI21 (jamba) provider ---


class TestAi21ProviderPreset:
    """AI21 Studio — OpenAI-compatible chat completions under /studio/v1."""

    def test_ai21_present_in_presets(self):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        assert "ai21" in PROVIDER_PRESETS

    def test_ai21_has_all_required_fields(self):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        preset = PROVIDER_PRESETS["ai21"]
        missing = REQUIRED_FIELDS - set(preset.keys())
        assert not missing, f"ai21 missing fields: {missing}"

    def test_ai21_api_base_url(self):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        assert PROVIDER_PRESETS["ai21"]["api_base_url"] == "https://api.ai21.com/studio/v1"

    def test_ai21_credential_env_var(self):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        assert PROVIDER_PRESETS["ai21"]["credential_env_var"] == "AI21_API_KEY"

    def test_ai21_display_name(self):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        assert PROVIDER_PRESETS["ai21"]["display_name"] == "AI21"

    def test_ai21_uses_openai_compatible_stack(self):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        preset = PROVIDER_PRESETS["ai21"]
        assert preset["provider_package"] == "langchain-openai"
        assert preset["provider_class"] == "ChatOpenAI"

    def test_ai21_aliases(self):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        preset = PROVIDER_PRESETS["ai21"]
        assert preset["credential_alias"] == "ai21_api_key"
        assert preset["api_base_alias"] == "ai21_api_base"

    def test_ai21_does_not_advertise_free_models(self):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        preset = PROVIDER_PRESETS["ai21"]
        assert preset["supports_free_models"] is False
        assert preset["free_models_endpoint"] is None

    def test_ai21_flagship_model_is_jamba(self):
        from general_ludd.models.provider_presets import PROVIDER_FLAGSHIP_MODELS

        assert PROVIDER_FLAGSHIP_MODELS["ai21"] == "jamba-1.5-large"

    def test_get_ai21_returns_correct_dict(self):
        from general_ludd.models.provider_presets import get_provider_preset

        preset = get_provider_preset("ai21")
        assert preset is not None
        assert preset["api_base_url"] == "https://api.ai21.com/studio/v1"
        assert preset["credential_env_var"] == "AI21_API_KEY"

    def test_get_ai21_is_case_insensitive(self):
        from general_ludd.models.provider_presets import get_provider_preset

        assert get_provider_preset("AI21") is not None
        assert get_provider_preset("Ai21") is not None

    def test_detect_ai21_credential(self):
        from general_ludd.models.provider_presets import detect_credential_alias

        assert detect_credential_alias("ai21", {"AI21_API_KEY": "k"}) is True
        assert detect_credential_alias("ai21", {}) is False
        assert detect_credential_alias("ai21", {"AI21_API_KEY": ""}) is False

    def test_list_configured_includes_ai21(self):
        from general_ludd.models.provider_presets import list_configured_providers

        configured = list_configured_providers({"AI21_API_KEY": "k"})
        assert "ai21" in configured


# --- Google (Gemini OpenAI-compat mode) ---

GOOGLE_PRESET = {
    "api_base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
    "provider_package": "langchain-openai",
    "provider_class": "ChatOpenAI",
    "credential_env_var": "GOOGLE_API_KEY",
    "credential_alias": "google_api_key",
    "api_base_alias": "google_api_base",
    "display_name": "Google",
    "free_models_endpoint": None,
    "supports_free_models": False,
}


class TestGoogleProviderPreset:
    def test_google_present_in_presets(self):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        assert "google" in PROVIDER_PRESETS

    def test_google_has_all_required_fields(self):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        preset = PROVIDER_PRESETS["google"]
        missing = REQUIRED_FIELDS - set(preset.keys())
        assert not missing, f"Provider 'google' missing fields: {missing}"

    def test_google_uses_openai_compatible_stack(self):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        preset = PROVIDER_PRESETS["google"]
        assert preset["provider_package"] == "langchain-openai"
        assert preset["provider_class"] == "ChatOpenAI"

    @pytest.mark.parametrize("field,expected", list(GOOGLE_PRESET.items()))
    def test_google_field_values_match_spec(self, field, expected):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        assert PROVIDER_PRESETS["google"][field] == expected, (
            f"google.{field} = {PROVIDER_PRESETS['google'][field]!r}, "
            f"expected {expected!r}"
        )

    def test_google_does_not_advertise_free_models(self):
        from general_ludd.models.provider_presets import PROVIDER_PRESETS

        preset = PROVIDER_PRESETS["google"]
        assert preset["supports_free_models"] is False
        assert preset["free_models_endpoint"] is None


class TestGetProviderPresetForGoogle:
    def test_get_google_returns_correct_dict(self):
        from general_ludd.models.provider_presets import get_provider_preset

        preset = get_provider_preset("google")
        assert preset is not None
        assert preset["api_base_url"] == (
            "https://generativelanguage.googleapis.com/v1beta/openai"
        )
        assert preset["credential_env_var"] == "GOOGLE_API_KEY"
        assert preset["display_name"] == "Google"
        assert preset["credential_alias"] == "google_api_key"
        assert preset["api_base_alias"] == "google_api_base"
        assert preset["provider_package"] == "langchain-openai"
        assert preset["provider_class"] == "ChatOpenAI"

    def test_get_google_is_case_insensitive(self):
        from general_ludd.models.provider_presets import get_provider_preset

        assert get_provider_preset("GOOGLE") is not None
        assert get_provider_preset("Google") is not None
        assert get_provider_preset("gOoGlE") is not None


class TestGoogleFlagshipModel:
    def test_google_flagship_is_gemini_25_pro(self):
        from general_ludd.models.provider_presets import PROVIDER_FLAGSHIP_MODELS

        assert PROVIDER_FLAGSHIP_MODELS["google"] == "gemini-2.5-pro"

    def test_get_helper_returns_gemini_for_google(self):
        from general_ludd.models.provider_presets import get_provider_flagship_model

        assert get_provider_flagship_model("google") == "gemini-2.5-pro"
        assert get_provider_flagship_model("GOOGLE") == "gemini-2.5-pro"


class TestDetectCredentialAliasForGoogle:
    def test_returns_true_when_google_api_key_set(self):
        from general_ludd.models.provider_presets import detect_credential_alias

        assert detect_credential_alias(
            "google", {"GOOGLE_API_KEY": "AIza-test-key"}
        ) is True

    def test_returns_false_when_google_api_key_missing(self):
        from general_ludd.models.provider_presets import detect_credential_alias

        assert detect_credential_alias("google", {}) is False

    def test_returns_false_when_google_api_key_empty(self):
        from general_ludd.models.provider_presets import detect_credential_alias

        assert detect_credential_alias("google", {"GOOGLE_API_KEY": ""}) is False


class TestListConfiguredProvidersWithGoogle:
    def test_lists_google_when_configured(self):
        from general_ludd.models.provider_presets import list_configured_providers

        configured = list_configured_providers({"GOOGLE_API_KEY": "key"})
        assert "google" in configured

    def test_google_mixed_with_other_providers(self):
        from general_ludd.models.provider_presets import list_configured_providers

        env = {
            "OPENAI_API_KEY": "oai",
            "GOOGLE_API_KEY": "goog",
            "MISTRAL_API_KEY": "mi",
        }
        configured = list_configured_providers(env)
        assert "google" in configured
        assert "openai" in configured
        assert "mistral" in configured


class TestAutoConfigureWithGoogle:
    def test_google_profile_uses_gemini_flagship(self):
        from general_ludd.models.auto_configurator import AutoConfigurator

        env = {"GOOGLE_API_KEY": "k"}
        configurator = AutoConfigurator()
        profiles = configurator.auto_configure_from_env(environ=env)
        assert len(profiles) == 1
        p = profiles[0]
        assert p["provider"] == "google"
        assert p["model_name"] == "gemini-2.5-pro"
        assert p["api_base_alias"] == "google_api_base"
        assert p["credential_alias"] == "google_api_key"
        assert p["provider_package"] == "langchain-openai"
        assert p["provider_class_hint"] == "ChatOpenAI"

    def test_google_profile_constructible_as_model_profile(self):
        from general_ludd.models.auto_configurator import AutoConfigurator
        from general_ludd.models.gateway import ModelProfile

        env = {"GOOGLE_API_KEY": "k"}
        configurator = AutoConfigurator()
        profiles = configurator.auto_configure_from_env(environ=env)
        mp = ModelProfile(**profiles[0])
        assert mp.enabled is True
        assert mp.model_name == "gemini-2.5-pro"
        assert mp.provider == "google"
