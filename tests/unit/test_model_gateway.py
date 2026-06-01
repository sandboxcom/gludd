"""Unit tests for enhanced model gateway and model router."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.models.gateway import ModelGateway, ModelProfile, ModelResponse
from general_ludd.models.provider_registry import ProviderRegistry
from general_ludd.models.router import ModelRouter
from general_ludd.secrets.manager import SecretAlias, SecretsManager


def _make_fake_chat_model():
    FakeChatModel = MagicMock()
    fake_instance = MagicMock()
    FakeChatModel.return_value = fake_instance
    return FakeChatModel, fake_instance


class TestModelProfileEnhanced:
    def test_profile_has_new_fields(self):
        p = ModelProfile(
            model_profile_id="test_enhanced",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            roles=["coder", "reviewer"],
            latency_class="fast",
            quality_class="high",
            fallback_profiles=["openrouter_coder"],
            probe_enabled=True,
        )
        assert p.provider_package == "langchain-openai"
        assert p.provider_class_hint == "ChatOpenAI"
        assert p.roles == ["coder", "reviewer"]
        assert p.latency_class == "fast"
        assert p.quality_class == "high"
        assert p.fallback_profiles == ["openrouter_coder"]
        assert p.probe_enabled is True

    def test_profile_defaults(self):
        p = ModelProfile(model_profile_id="defaults_test")
        assert p.roles == []
        assert p.fallback_profiles == []
        assert p.probe_enabled is False
        assert p.latency_class is None
        assert p.quality_class is None


class TestModelGatewayCallModelWithStub:
    def _make_gateway(self) -> tuple[ModelGateway, ProviderRegistry, SecretsManager]:
        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")

        fake_secret_client = MagicMock()
        fake_secret_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"value": "sk-test-key"}}
        }
        secrets = SecretsManager(
            client=fake_secret_client,
            aliases={"openai_key": SecretAlias("openai_key", "keys/openai", "secret")},
        )

        profile = ModelProfile(
            model_profile_id="gpt4",
            enabled=True,
            provider="openai",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="gpt-4",
            credential_alias="openai_key",
            run_budget_usd=100.0,
        )

        gw = ModelGateway(
            profiles=[profile],
            provider_registry=reg,
            secrets_manager=secrets,
        )
        return gw, reg, secrets

    def test_call_model_returns_response(self):
        gw, reg, _ = self._make_gateway()

        FakeChatModel, fake_instance = _make_fake_chat_model()
        fake_instance.invoke.return_value = MagicMock(
            content="Hello!",
            usage_metadata={"input_tokens": 10, "output_tokens": 5},
        )
        FakeChatModel.return_value = fake_instance

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
        ):
            resp = gw.call_model("gpt4", [{"role": "user", "content": "hi"}])

        assert isinstance(resp, ModelResponse)
        assert resp.content == "Hello!"
        assert resp.usage_metadata["input_tokens"] == 10

    def test_call_model_raises_for_missing_profile(self):
        gw = ModelGateway()
        with pytest.raises(ValueError, match="not found"):
            gw.call_model("nonexistent", [])

    def test_call_model_rejects_over_budget(self):
        gw, reg, _ = self._make_gateway()
        with (
            patch.object(reg, "is_installed", return_value=True),
            pytest.raises(ValueError, match="budget"),
        ):
            gw.call_model(
                "gpt4",
                [{"role": "user", "content": "hi"}],
                estimated_cost=999.0,
                budget_remaining=1.0,
            )

    def test_call_model_creates_dep_todo_for_missing_provider(self):
        gw, reg, _ = self._make_gateway()

        with (
            patch.object(reg, "is_installed", return_value=False),
            patch.object(reg, "install_provider", return_value=MagicMock()) as mock_install,
            pytest.raises(ImportError, match="not installed"),
        ):
            gw.call_model("gpt4", [{"role": "user", "content": "hi"}])
        mock_install.assert_called_once_with("openai")


class TestModelGatewayRecordsUsage:
    def test_usage_metadata_recorded(self):
        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")

        fake_secret_client = MagicMock()
        fake_secret_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"value": "sk-test-key"}}
        }
        secrets = SecretsManager(
            client=fake_secret_client,
            aliases={"openai_key": SecretAlias("openai_key", "keys/openai", "secret")},
        )

        profile = ModelProfile(
            model_profile_id="gpt4_usage",
            enabled=True,
            provider="openai",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="gpt-4",
            credential_alias="openai_key",
            cost_per_input_token=0.01,
            cost_per_output_token=0.03,
            run_budget_usd=100.0,
        )

        gw = ModelGateway(
            profiles=[profile],
            provider_registry=reg,
            secrets_manager=secrets,
        )

        FakeChatModel, fake_instance = _make_fake_chat_model()
        fake_instance.invoke.return_value = MagicMock(
            content="result",
            usage_metadata={"input_tokens": 100, "output_tokens": 50},
        )
        FakeChatModel.return_value = fake_instance

        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
        ):
            resp = gw.call_model("gpt4_usage", [{"role": "user", "content": "hi"}])

        expected_cost = 100 * 0.01 + 50 * 0.03
        assert resp.cost_estimate == pytest.approx(expected_cost)


class TestModelGatewayRedactsCredentials:
    def test_credentials_redacted_in_logs(self, caplog):
        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")

        fake_secret_client = MagicMock()
        fake_secret_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"value": "sk-super-secret-key-12345"}}
        }
        secrets = SecretsManager(
            client=fake_secret_client,
            aliases={"openai_key": SecretAlias("openai_key", "keys/openai", "secret")},
        )

        profile = ModelProfile(
            model_profile_id="gpt4_redact",
            enabled=True,
            provider="openai",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="gpt-4",
            credential_alias="openai_key",
            run_budget_usd=100.0,
        )

        gw = ModelGateway(
            profiles=[profile],
            provider_registry=reg,
            secrets_manager=secrets,
        )

        FakeChatModel, fake_instance = _make_fake_chat_model()
        fake_instance.invoke.return_value = MagicMock(
            content="ok",
            usage_metadata={"input_tokens": 1, "output_tokens": 1},
        )
        FakeChatModel.return_value = fake_instance

        with (
            caplog.at_level(logging.DEBUG, logger="general_ludd.models.gateway"),
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
        ):
            gw.call_model("gpt4_redact", [{"role": "user", "content": "hi"}])

        for record in caplog.records:
            assert "sk-super-secret-key-12345" not in record.message


class TestModelRouter:
    def test_resolve_role_returns_profile_id(self):
        router = ModelRouter(role_mapping={"coder": "gpt4", "reviewer": "claude3"})
        assert router.resolve_role("coder") == "gpt4"
        assert router.resolve_role("reviewer") == "claude3"

    def test_unmapped_role_returns_none(self):
        router = ModelRouter(role_mapping={"coder": "gpt4"})
        assert router.resolve_role("unknown") is None

    def test_add_role_mapping(self):
        router = ModelRouter()
        router.add_role("planner", "gpt4")
        assert router.resolve_role("planner") == "gpt4"

    def test_list_roles(self):
        router = ModelRouter(role_mapping={"coder": "gpt4", "reviewer": "claude3"})
        roles = router.list_roles()
        assert "coder" in roles
        assert "reviewer" in roles


class TestLocalModelProfileIgnoredUnlessEnabled:
    def test_local_model_disabled(self):
        gw = ModelGateway([ModelProfile(
            model_profile_id="local_llm",
            enabled=False,
            resource_profile="local_heavy",
        )])
        assert gw.is_available("local_llm") is False

    def test_local_model_enabled(self):
        gw = ModelGateway([ModelProfile(
            model_profile_id="local_llm",
            enabled=True,
            resource_profile="local_heavy",
        )])
        assert gw.is_available("local_llm") is True


class TestExampleConfigsAreValidYaml:
    def test_example_configs_are_valid_yaml(self):
        import os

        import yaml

        config_dir = os.path.join(
            os.path.dirname(__file__), "..", "..", "config", "model_profiles"
        )
        config_dir = os.path.normpath(config_dir)
        if not os.path.isdir(config_dir):
            pytest.skip("config/model_profiles directory not found")

        files = [f for f in os.listdir(config_dir) if f.endswith((".yml", ".yaml"))]
        assert len(files) > 0, "No YAML config files found"

        for fname in files:
            with open(os.path.join(config_dir, fname)) as fh:
                data = yaml.safe_load(fh)
            assert isinstance(data, dict), f"{fname} did not parse to a dict"
            assert "model_profile_id" in data, f"{fname} missing model_profile_id"
            assert "provider" in data, f"{fname} missing provider"
            assert "provider_package" in data, f"{fname} missing provider_package"
