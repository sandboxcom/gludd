"""Tests for secrets wiring and model profile loading at daemon startup."""

from __future__ import annotations

import os

import yaml

from general_ludd.daemon import build_secrets_resolver, load_model_profiles


class TestBuildSecretsResolver:
    def test_returns_env_secrets_when_no_openbao(self):
        resolver = build_secrets_resolver(openbao_config=None)
        assert resolver is not None
        assert resolver.resolve("NONEXISTENT") is None

    def test_env_resolver_reads_environ(self):
        os.environ["_GLUDD_TEST_KEY"] = "testval"
        try:
            resolver = build_secrets_resolver(openbao_config=None)
            assert resolver.resolve("_GLUDD_TEST_KEY") == "testval"
        finally:
            del os.environ["_GLUDD_TEST_KEY"]

    def test_returns_env_resolver_when_openbao_disabled(self):
        from general_ludd.secrets.config import OpenBaoConfig

        cfg = OpenBaoConfig(mode="disabled")
        resolver = build_secrets_resolver(openbao_config=cfg)
        assert resolver is not None

    def test_resolver_protocol_satisfies_gateway(self):
        resolver = build_secrets_resolver(openbao_config=None)
        assert hasattr(resolver, "resolve")
        assert callable(resolver.resolve)

    def test_env_resolver_with_overrides(self):
        resolver = build_secrets_resolver(
            openbao_config=None,
            env_overrides={"MY_API_KEY": "sk-test-123"},
        )
        assert resolver.resolve("MY_API_KEY") == "sk-test-123"


class TestLoadModelProfiles:
    def test_loads_profiles_from_directory(self, tmp_path):
        profiles_dir = tmp_path / "model_profiles"
        profiles_dir.mkdir()
        (profiles_dir / "test_profile.yml").write_text(
            yaml.dump(
                {
                    "model_profile_id": "test_prof",
                    "provider": "openai",
                    "model_name": "gpt-4",
                    "credential_alias": "OPENAI_API_KEY",
                    "enabled": True,
                }
            )
        )
        profiles = load_model_profiles(profiles_dir=str(profiles_dir))
        assert len(profiles) >= 1
        assert any(p.model_profile_id == "test_prof" for p in profiles)

    def test_profile_has_credential_alias(self, tmp_path):
        profiles_dir = tmp_path / "model_profiles"
        profiles_dir.mkdir()
        (profiles_dir / "zai.yml").write_text(
            yaml.dump(
                {
                    "model_profile_id": "zai_test",
                    "credential_alias": "ZAI_API_KEY",
                    "api_base_alias": "ZAI_BASE_URL",
                    "model_name": "glm-5.1",
                    "provider": "openai",
                }
            )
        )
        profiles = load_model_profiles(profiles_dir=str(profiles_dir))
        zai = next(p for p in profiles if p.model_profile_id == "zai_test")
        assert zai.credential_alias == "ZAI_API_KEY"
        assert zai.api_base_alias == "ZAI_BASE_URL"

    def test_empty_dir_returns_empty_list(self, tmp_path):
        profiles_dir = tmp_path / "model_profiles"
        profiles_dir.mkdir()
        profiles = load_model_profiles(profiles_dir=str(profiles_dir))
        assert profiles == []

    def test_missing_dir_returns_empty_list(self):
        profiles = load_model_profiles(profiles_dir="/nonexistent/path")
        assert profiles == []

    def test_loads_repo_config_profiles(self):
        profiles = load_model_profiles(
            profiles_dir="config/model_profiles",
        )
        assert len(profiles) >= 1
        ids = [p.model_profile_id for p in profiles]
        assert "zai_coder" in ids


class TestCredentialResolutionEndToEnd:
    def test_env_resolver_provides_key_to_gateway(self):
        from general_ludd.models.gateway import ModelGateway, ModelProfile
        from general_ludd.models.provider_registry import ProviderRegistry

        resolver = build_secrets_resolver(
            openbao_config=None,
            env_overrides={"TEST_API_KEY": "sk-test-key"},
        )
        profile = ModelProfile(
            model_profile_id="test",
            credential_alias="TEST_API_KEY",
            model_name="test-model",
        )
        gateway = ModelGateway(
            profiles=[profile],
            secrets_manager=resolver,
            provider_registry=ProviderRegistry(),
        )
        listed = gateway.list_profiles()
        assert any(p.model_profile_id == "test" for p in listed)

    def test_no_credential_alias_means_no_key(self, tmp_path):
        profiles_dir = tmp_path / "model_profiles"
        profiles_dir.mkdir()
        (profiles_dir / "local.yml").write_text(
            yaml.dump(
                {
                    "model_profile_id": "local_vllm",
                    "credential_alias": None,
                    "api_base_alias": "VLLM_BASE_URL",
                    "model_name": "local-model",
                    "provider": "openai",
                }
            )
        )
        profiles = load_model_profiles(profiles_dir=str(profiles_dir))
        local = next(p for p in profiles if p.model_profile_id == "local_vllm")
        assert local.credential_alias is None
