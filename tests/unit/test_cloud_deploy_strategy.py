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


class TestDefaultAzureModel:
    def test_is_known_model(self):
        assert DEFAULT_AZURE_MODEL == "Qwen/Qwen2.5-0.5B-Instruct"


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
