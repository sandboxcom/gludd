"""Tests for deploy_strategy — Azure gateway factory."""

from __future__ import annotations

import os
from unittest import mock

from general_ludd.cloud.deploy_strategy import (
    DEFAULT_AZURE_MODEL,
    _openai_base_url,
    build_azure_gateway,
)
from general_ludd.models.gateway import ModelGateway


class TestOpenaiBaseUrl:
    def test_already_v1_endpoint(self):
        assert _openai_base_url("https://example.com/v1") == "https://example.com/v1"

    def test_adds_v1_suffix(self):
        assert _openai_base_url("https://example.com") == "https://example.com/v1"

    def test_trailing_slash_removed(self):
        assert _openai_base_url("https://example.com/") == "https://example.com/v1"

    def test_trailing_slash_v1(self):
        assert _openai_base_url("https://example.com/v1/") == "https://example.com/v1"

    def test_deep_path_with_v1(self):
        assert _openai_base_url("https://example.com/api/v1") == "https://example.com/api/v1"

    def test_deep_path_with_v1_trailing_slash(self):
        assert _openai_base_url("https://example.com/inference/v1/") == "https://example.com/inference/v1"

    def test_ip_address_with_port(self):
        assert _openai_base_url("http://10.0.0.1:8000") == "http://10.0.0.1:8000/v1"

    def test_port_with_v1(self):
        assert _openai_base_url("http://127.0.0.1:8080/v1") == "http://127.0.0.1:8080/v1"

    def test_empty_string(self):
        assert _openai_base_url("") == "/v1"

    def test_whitespace_only(self):
        assert _openai_base_url("  ") == "  /v1"

    def test_subpath_with_multiple_slashes(self):
        assert _openai_base_url("https://host/api/v1/chat/") == "https://host/api/v1/chat/v1"

    def test_starts_with_v1_no_host(self):
        assert _openai_base_url("/v1") == "/v1"


class TestDefaultAzureModel:
    def test_is_known_model(self):
        assert DEFAULT_AZURE_MODEL == "Qwen/Qwen2.5-0.5B-Instruct"

    def test_default_model_is_non_empty(self):
        assert len(DEFAULT_AZURE_MODEL) > 0


class TestBuildAzureGateway:
    def test_returns_none_when_no_endpoint(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            assert build_azure_gateway() is None

    def test_returns_none_when_empty_endpoint(self):
        assert build_azure_gateway(base_url="   ") is None

    def test_returns_gateway_when_endpoint_provided(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            gateway = build_azure_gateway(base_url="https://my-azure-gpu.com/v1", model_name="gpt-4")
            assert isinstance(gateway, ModelGateway)
            profiles = gateway.list_profiles()
            assert len(profiles) == 2
            profile_ids = {p.model_profile_id for p in profiles}
            assert profile_ids == {"default", "azure_self_improve"}

    def test_gateway_picks_model_from_env(self):
        env = {"AZURE_BASE_URL": "https://gpu.example.com/v1", "AZURE_MODEL": "custom-model"}
        with mock.patch.dict(os.environ, env, clear=True):
            gateway = build_azure_gateway()
            assert isinstance(gateway, ModelGateway)
            for profile in gateway.list_profiles():
                assert profile.model_name == "custom-model"

    def test_gateway_falls_back_to_default_model(self):
        env = {"AZURE_BASE_URL": "https://gpu.example.com/v1"}
        with mock.patch.dict(os.environ, env, clear=True):
            gateway = build_azure_gateway()
            assert isinstance(gateway, ModelGateway)
            for profile in gateway.list_profiles():
                assert profile.model_name == DEFAULT_AZURE_MODEL

    def test_can_get_default_profile(self):
        env = {"AZURE_BASE_URL": "https://gpu.example.com/v1"}
        with mock.patch.dict(os.environ, env, clear=True):
            gateway = build_azure_gateway()
            assert isinstance(gateway, ModelGateway)
            default = gateway.get_profile("default")
            assert default is not None
            assert default.model_profile_id == "default"

    def test_can_get_azure_self_improve_profile(self):
        env = {"AZURE_BASE_URL": "https://gpu.example.com/v1"}
        with mock.patch.dict(os.environ, env, clear=True):
            gateway = build_azure_gateway()
            assert isinstance(gateway, ModelGateway)
            profile = gateway.get_profile("azure_self_improve")
            assert profile is not None
            assert profile.model_profile_id == "azure_self_improve"


class TestBuildAzureGatewaySecrets:
    def test_secrets_manager_populates_base_url(self):
        env = {"AZURE_BASE_URL": "https://gpu.example.com"}
        with mock.patch.dict(os.environ, env, clear=True):
            gateway = build_azure_gateway()
            assert isinstance(gateway, ModelGateway)
            stored = gateway._secrets.resolve("AZURE_BASE_URL")
            assert stored == "https://gpu.example.com/v1"

    def test_secrets_manager_sets_fallback_api_key(self):
        env = {"AZURE_BASE_URL": "https://gpu.example.com/v1"}
        with mock.patch.dict(os.environ, env, clear=True):
            gateway = build_azure_gateway()
            assert isinstance(gateway, ModelGateway)
            stored = gateway._secrets.resolve("AZURE_API_KEY")
            assert stored == "not-required"

    def test_azure_api_key_preferred_over_openai_variant(self):
        env = {
            "AZURE_BASE_URL": "https://gpu.example.com/v1",
            "AZURE_API_KEY": "key-azure",
            "AZURE_OPENAI_API_KEY": "key-openai",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            gateway = build_azure_gateway()
            assert isinstance(gateway, ModelGateway)
            stored = gateway._secrets.resolve("AZURE_API_KEY")
            assert stored == "key-azure"

    def test_azure_openai_api_key_used_when_main_key_missing(self):
        env = {
            "AZURE_BASE_URL": "https://gpu.example.com/v1",
            "AZURE_OPENAI_API_KEY": "key-openai-only",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            gateway = build_azure_gateway()
            assert isinstance(gateway, ModelGateway)
            stored = gateway._secrets.resolve("AZURE_API_KEY")
            assert stored == "key-openai-only"


class TestBuildAzureGatewayProfiles:
    def test_profiles_have_openai_provider(self):
        env = {"AZURE_BASE_URL": "https://gpu.example.com/v1"}
        with mock.patch.dict(os.environ, env, clear=True):
            gateway = build_azure_gateway()
            for profile in gateway.list_profiles():
                assert profile.provider == "openai"

    def test_profiles_have_provider_package(self):
        env = {"AZURE_BASE_URL": "https://gpu.example.com/v1"}
        with mock.patch.dict(os.environ, env, clear=True):
            gateway = build_azure_gateway()
            for profile in gateway.list_profiles():
                assert profile.provider_package == "langchain_openai"

    def test_profiles_have_provider_class_hint(self):
        env = {"AZURE_BASE_URL": "https://gpu.example.com/v1"}
        with mock.patch.dict(os.environ, env, clear=True):
            gateway = build_azure_gateway()
            for profile in gateway.list_profiles():
                assert profile.provider_class_hint == "ChatOpenAI"

    def test_profiles_are_enabled(self):
        env = {"AZURE_BASE_URL": "https://gpu.example.com/v1"}
        with mock.patch.dict(os.environ, env, clear=True):
            gateway = build_azure_gateway()
            for profile in gateway.list_profiles():
                assert profile.enabled is True

    def test_profiles_not_api_metered(self):
        env = {"AZURE_BASE_URL": "https://gpu.example.com/v1"}
        with mock.patch.dict(os.environ, env, clear=True):
            gateway = build_azure_gateway()
            for profile in gateway.list_profiles():
                assert profile.api_metered is False

    def test_profiles_have_credential_alias(self):
        env = {"AZURE_BASE_URL": "https://gpu.example.com/v1"}
        with mock.patch.dict(os.environ, env, clear=True):
            gateway = build_azure_gateway()
            for profile in gateway.list_profiles():
                assert profile.credential_alias == "AZURE_API_KEY"

    def test_profiles_have_api_base_alias(self):
        env = {"AZURE_BASE_URL": "https://gpu.example.com/v1"}
        with mock.patch.dict(os.environ, env, clear=True):
            gateway = build_azure_gateway()
            for profile in gateway.list_profiles():
                assert profile.api_base_alias == "AZURE_BASE_URL"


class TestBuildAzureGatewayProviderRegistry:
    def test_registry_registers_openai_provider(self):
        env = {"AZURE_BASE_URL": "https://gpu.example.com/v1"}
        with mock.patch.dict(os.environ, env, clear=True):
            gateway = build_azure_gateway()
            providers = gateway._registry.list_providers()
            assert "openai" in providers

    def test_registry_has_provider_class_hint(self):
        env = {"AZURE_BASE_URL": "https://gpu.example.com/v1"}
        with mock.patch.dict(os.environ, env, clear=True):
            gateway = build_azure_gateway()
            from general_ludd.models.provider_registry import ProviderRegistry

            registry = gateway._registry
            assert isinstance(registry, ProviderRegistry)


class TestBuildAzureGatewayBaseUrlVariants:
    def test_base_url_without_v1_normalized(self):
        gateway = build_azure_gateway(base_url="https://vllm.internal:8000", model_name="m")
        assert isinstance(gateway, ModelGateway)
        stored = gateway._secrets.resolve("AZURE_BASE_URL")
        assert stored == "https://vllm.internal:8000/v1"

    def test_env_url_takes_trailing_whitespace_stripped(self):
        env = {"AZURE_BASE_URL": "  https://gpu.example.com  "}
        with mock.patch.dict(os.environ, env, clear=True):
            gateway = build_azure_gateway()
            assert isinstance(gateway, ModelGateway)
            stored = gateway._secrets.resolve("AZURE_BASE_URL")
            assert stored == "https://gpu.example.com/v1"

    def test_model_name_param_overrides_env(self):
        env = {"AZURE_BASE_URL": "https://gpu.example.com/v1", "AZURE_MODEL": "env-model"}
        with mock.patch.dict(os.environ, env, clear=True):
            gateway = build_azure_gateway(model_name="param-model")
            assert isinstance(gateway, ModelGateway)
            for profile in gateway.list_profiles():
                assert profile.model_name == "param-model"

    def test_gateway_not_constructed_when_env_base_url_empty(self):
        env = {"AZURE_BASE_URL": ""}
        with mock.patch.dict(os.environ, env, clear=True):
            gateway = build_azure_gateway()
            assert gateway is None

    def test_gateway_not_constructed_when_env_base_url_whitespace(self):
        env = {"AZURE_BASE_URL": "   "}
        with mock.patch.dict(os.environ, env, clear=True):
            gateway = build_azure_gateway()
            assert gateway is None

    def test_base_url_param_overrides_env(self):
        env = {"AZURE_BASE_URL": "https://env-url.com/v1"}
        with mock.patch.dict(os.environ, env, clear=True):
            gateway = build_azure_gateway(base_url="https://param-url.com/v1", model_name="m")
            assert isinstance(gateway, ModelGateway)
            stored = gateway._secrets.resolve("AZURE_BASE_URL")
            assert stored == "https://param-url.com/v1"


class TestBuildAzureGatewayEdgeCases:
    def test_model_name_whitespace_stripped(self):
        gateway = build_azure_gateway(base_url="https://g.example.com/v1", model_name="  gpt-4  ")
        assert isinstance(gateway, ModelGateway)
        for profile in gateway.list_profiles():
            assert profile.model_name == "gpt-4"

    def test_get_nonexistent_profile(self):
        env = {"AZURE_BASE_URL": "https://gpu.example.com/v1"}
        with mock.patch.dict(os.environ, env, clear=True):
            gateway = build_azure_gateway()
            profile = gateway.get_profile("nonexistent")
            assert profile is None

    def test_multiple_builds_independent(self):
        env = {"AZURE_BASE_URL": "https://gpu.example.com/v1"}
        with mock.patch.dict(os.environ, env, clear=True):
            gw1 = build_azure_gateway(model_name="model-a")
            gw2 = build_azure_gateway(model_name="model-b")
            assert isinstance(gw1, ModelGateway)
            assert isinstance(gw2, ModelGateway)
            for p in gw1.list_profiles():
                assert p.model_name == "model-a"
            for p in gw2.list_profiles():
                assert p.model_name == "model-b"
