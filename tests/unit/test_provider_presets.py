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
