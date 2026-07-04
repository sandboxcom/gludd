"""Unit tests for LangChainHubRegistry and PromptRegistry hub integration."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.prompts.hub_registry import LangChainHubRegistry
from general_ludd.prompts.registry import PromptRegistry


@pytest.fixture
def clean_hub_registry():
    """A fresh LangChainHubRegistry with no cached state."""
    return LangChainHubRegistry()


@pytest.fixture
def hub_registry_with_flag():
    """Hub registry with use_hub=True."""
    return LangChainHubRegistry(use_hub=True, default_tag="production")


class TestLangChainHubRegistryStandalone:
    """Tests for LangChainHubRegistry in isolation."""

    def test_use_hub_defaults_to_false(self, clean_hub_registry):
        assert clean_hub_registry.use_hub is False
        assert clean_hub_registry.default_tag == "production"

    def test_load_template_returns_none_when_hub_disabled(self, clean_hub_registry):
        result = clean_hub_registry.load_template("my-org/my-prompt")
        assert result is None

    def test_load_template_returns_none_when_hub_not_installed(self):
        with patch.object(
            sys.modules.get("general_ludd.prompts.hub_registry", sys.modules[__name__]),
            "HAS_LANGCHAIN_HUB",
            False,
        ):
            reg = LangChainHubRegistry(use_hub=True)
            result = reg.load_template("my-org/my-prompt")
            assert result is None

    @patch("general_ludd.prompts.hub_registry.HAS_LANGCHAIN_HUB", True)
    @patch("general_ludd.prompts.hub_registry.langchain_hub")
    def test_load_template_pulls_and_returns_template_text(self, mock_hub, hub_registry_with_flag):
        mock_prompt = MagicMock()
        mock_prompt.template = "You are a helpful assistant."
        mock_prompt.metadata = {"lc_hub_commit_hash": "abc123def"}  # pragma: allowlist secret
        mock_prompt.messages = None
        del mock_prompt.messages
        mock_hub.pull.return_value = mock_prompt

        result = hub_registry_with_flag.load_template("my-org/my-prompt")

        mock_hub.pull.assert_called_once_with("my-org/my-prompt:production")
        assert result == "You are a helpful assistant."

    @patch("general_ludd.prompts.hub_registry.HAS_LANGCHAIN_HUB", True)
    @patch("general_ludd.prompts.hub_registry.langchain_hub")
    def test_load_template_uses_custom_tag(self, mock_hub, hub_registry_with_flag):
        mock_prompt = MagicMock()
        mock_prompt.template = "Staging template content."
        mock_prompt.metadata = {"lc_hub_commit_hash": "def456abc"}  # pragma: allowlist secret
        mock_prompt.messages = None
        del mock_prompt.messages
        mock_hub.pull.return_value = mock_prompt

        result = hub_registry_with_flag.load_template("my-org/my-prompt", tag="staging")

        mock_hub.pull.assert_called_once_with("my-org/my-prompt:staging")
        assert result == "Staging template content."

    @patch("general_ludd.prompts.hub_registry.HAS_LANGCHAIN_HUB", True)
    @patch("general_ludd.prompts.hub_registry.langchain_hub")
    def test_load_template_preserves_explicit_tag_in_name(self, mock_hub, hub_registry_with_flag):
        mock_prompt = MagicMock()
        mock_prompt.template = "Explicit tag content."
        mock_prompt.metadata = {"lc_hub_commit_hash": "ghi789jkl"}  # pragma: allowlist secret
        mock_prompt.messages = None
        del mock_prompt.messages
        mock_hub.pull.return_value = mock_prompt

        result = hub_registry_with_flag.load_template("my-org/my-prompt:staging")

        mock_hub.pull.assert_called_once_with("my-org/my-prompt:staging")
        assert result == "Explicit tag content."

    @patch("general_ludd.prompts.hub_registry.HAS_LANGCHAIN_HUB", True)
    @patch("general_ludd.prompts.hub_registry.langchain_hub")
    def test_load_template_returns_none_on_hub_exception(self, mock_hub, hub_registry_with_flag):
        mock_hub.pull.side_effect = RuntimeError("Network unreachable")

        result = hub_registry_with_flag.load_template("my-org/my-prompt")
        assert result is None

    @patch("general_ludd.prompts.hub_registry.HAS_LANGCHAIN_HUB", True)
    @patch("general_ludd.prompts.hub_registry.langchain_hub")
    def test_load_template_chat_prompt_messages(self, mock_hub, hub_registry_with_flag):
        human_msg = MagicMock()
        human_msg.prompt = MagicMock()
        human_msg.prompt.template = "Hello from human."
        human_msg.type = "human"
        human_msg.content = None
        del human_msg.content

        ai_msg = MagicMock()
        ai_msg.prompt = MagicMock()
        ai_msg.prompt.template = "Hello from AI."
        ai_msg.type = "ai"
        ai_msg.content = None
        del ai_msg.content

        mock_prompt = MagicMock()
        mock_prompt.messages = [human_msg, ai_msg]
        mock_prompt.template = None
        del mock_prompt.template
        mock_prompt.metadata = {"lc_hub_commit_hash": "chat123"}

        mock_hub.pull.return_value = mock_prompt

        result = hub_registry_with_flag.load_template("my-org/chat-prompt")
        assert result is not None
        assert "[human]\nHello from human." in result
        assert "[ai]\nHello from AI." in result

    @patch("general_ludd.prompts.hub_registry.HAS_LANGCHAIN_HUB", True)
    @patch("general_ludd.prompts.hub_registry.langchain_hub")
    def test_load_template_chat_content_fallback(self, mock_hub, hub_registry_with_flag):
        human_msg = MagicMock()
        human_msg.content = "Hello from human."
        human_msg.type = "human"
        human_msg.prompt = None
        del human_msg.prompt

        mock_prompt = MagicMock()
        mock_prompt.messages = [human_msg]
        mock_prompt.template = None
        del mock_prompt.template
        mock_prompt.metadata = {"lc_hub_commit_hash": "chat456"}

        mock_hub.pull.return_value = mock_prompt

        result = hub_registry_with_flag.load_template("my-org/chat-content")
        assert result is not None
        assert "[human]\nHello from human." in result

    @patch("general_ludd.prompts.hub_registry.HAS_LANGCHAIN_HUB", True)
    @patch("general_ludd.prompts.hub_registry.langchain_hub")
    def test_load_template_no_extractable_text(self, mock_hub, hub_registry_with_flag):
        mock_prompt = MagicMock()
        mock_prompt.template = None
        del mock_prompt.template
        mock_prompt.messages = None
        del mock_prompt.messages
        mock_prompt.metadata = {"lc_hub_commit_hash": "empty123"}

        mock_hub.pull.return_value = mock_prompt

        result = hub_registry_with_flag.load_template("my-org/empty-prompt")
        assert result is None

    def test_list_hub_templates_empty_by_default(self, clean_hub_registry):
        assert clean_hub_registry.list_hub_templates() == []

    @patch("general_ludd.prompts.hub_registry.HAS_LANGCHAIN_HUB", True)
    @patch("general_ludd.prompts.hub_registry.langchain_hub")
    def test_list_hub_templates_after_load(self, mock_hub, hub_registry_with_flag):
        mock_prompt = MagicMock()
        mock_prompt.template = "Content."
        mock_prompt.metadata = {"lc_hub_commit_hash": "abc123"}
        mock_prompt.messages = None
        del mock_prompt.messages
        mock_hub.pull.return_value = mock_prompt

        hub_registry_with_flag.load_template("org/prompt-a")
        hub_registry_with_flag.load_template("org/prompt-b")

        templates = hub_registry_with_flag.list_hub_templates()
        assert sorted(templates) == ["org/prompt-a", "org/prompt-b"]

    def test_get_version_info_unknown_returns_empty(self, clean_hub_registry):
        assert clean_hub_registry.get_version_info("nonexistent") == {}

    @patch("general_ludd.prompts.hub_registry.HAS_LANGCHAIN_HUB", True)
    @patch("general_ludd.prompts.hub_registry.langchain_hub")
    def test_get_version_info_returns_hub_commit(self, mock_hub, hub_registry_with_flag):
        mock_prompt = MagicMock()
        mock_prompt.template = "Content."
        mock_prompt.metadata = {"lc_hub_commit_hash": "deadbeef1234"}  # pragma: allowlist secret
        mock_prompt.messages = None
        del mock_prompt.messages
        mock_hub.pull.return_value = mock_prompt

        hub_registry_with_flag.load_template("org/my-prompt")

        info = hub_registry_with_flag.get_version_info("org/my-prompt")
        assert info == {"source": "langchain_hub", "hash": "deadbeef1234"}  # pragma: allowlist secret

    @patch("general_ludd.prompts.hub_registry.HAS_LANGCHAIN_HUB", True)
    @patch("general_ludd.prompts.hub_registry.langchain_hub")
    def test_get_version_info_no_hash_in_metadata(self, mock_hub, hub_registry_with_flag):
        mock_prompt = MagicMock()
        mock_prompt.template = "Content."
        mock_prompt.metadata = {}
        mock_prompt.messages = None
        del mock_prompt.messages
        mock_hub.pull.return_value = mock_prompt

        hub_registry_with_flag.load_template("org/no-commit-prompt")

        info = hub_registry_with_flag.get_version_info("org/no-commit-prompt")
        assert info == {}


class TestPromptRegistryHubIntegration:
    """Tests for PromptRegistry when a LangChainHubRegistry is wired in."""

    def test_list_templates_combines_local_and_hub(self):
        hub = LangChainHubRegistry()
        hub._hub_commits["org/hub-prompt"] = "abc123"
        reg = PromptRegistry(hub_registry=hub)
        reg.register("local-template", "Local content.")

        templates = reg.list_templates()
        assert "local-template" in templates
        assert "org/hub-prompt" in templates

    def test_list_templates_no_hub_registry(self):
        reg = PromptRegistry()
        reg.register("a", "template a")
        reg.register("b", "template b")
        assert reg.list_templates() == ["a", "b"]

    def test_get_version_info_prefers_hub_when_available(self):
        hub = LangChainHubRegistry()
        hub._hub_commits["org/my-prompt"] = "hubhash999"
        reg = PromptRegistry(hub_registry=hub)
        reg.register("org/my-prompt", "local content.")

        info = reg.get_template_version_info("org/my-prompt")
        assert info == {"source": "langchain_hub", "hash": "hubhash999"}

    def test_get_version_info_falls_back_to_local_when_hub_unknown(self):
        hub = LangChainHubRegistry()
        reg = PromptRegistry(hub_registry=hub)
        reg.register("local-only", "local content.")

        info = reg.get_template_version_info("local-only")
        assert "hash" in info
        assert info["hash"] is not None
        assert len(info["hash"]) == 64

    def test_get_version_info_no_hub_uses_local(self):
        reg = PromptRegistry()
        reg.register("t", "content")
        info = reg.get_template_version_info("t")
        assert "hash" in info
        assert info["hash"] is not None
        assert len(info["hash"]) == 64

    @patch("general_ludd.prompts.hub_registry.HAS_LANGCHAIN_HUB", True)
    @patch("general_ludd.prompts.hub_registry.langchain_hub")
    def test_render_falls_back_to_local_when_hub_fails(self, mock_hub, tmp_path):
        mock_hub.pull.side_effect = RuntimeError("Hub down")
        hub = LangChainHubRegistry(use_hub=True)
        reg = PromptRegistry(hub_registry=hub)
        reg.register("local-only", "Hello {{ name }}!")

        result = reg.render("local-only", name="World")
        assert result == "Hello World!"

    @patch("general_ludd.prompts.hub_registry.HAS_LANGCHAIN_HUB", True)
    @patch("general_ludd.prompts.hub_registry.langchain_hub")
    def test_render_uses_hub_when_template_not_registered_locally(self, mock_hub):
        mock_prompt = MagicMock()
        mock_prompt.template = "Hub says {{ greeting }}"
        mock_prompt.metadata = {"lc_hub_commit_hash": "hubhash111"}
        mock_prompt.messages = None
        del mock_prompt.messages
        mock_hub.pull.return_value = mock_prompt

        hub = LangChainHubRegistry(use_hub=True)
        reg = PromptRegistry(hub_registry=hub)

        result = reg.render("org/hub-only", greeting="hello")
        assert result == "Hub says hello"

    @patch("general_ludd.prompts.hub_registry.HAS_LANGCHAIN_HUB", True)
    @patch("general_ludd.prompts.hub_registry.langchain_hub")
    def test_render_prefers_local_over_hub(self, mock_hub, tmp_path):
        mock_prompt = MagicMock()
        mock_prompt.template = "Hub version."
        mock_prompt.metadata = {"lc_hub_commit_hash": "hubhash222"}
        mock_prompt.messages = None
        del mock_prompt.messages
        mock_hub.pull.return_value = mock_prompt

        hub = LangChainHubRegistry(use_hub=True)
        reg = PromptRegistry(hub_registry=hub)
        reg.register("org/shared-name", "Local version.")

        result = reg.render("org/shared-name")
        assert result == "Local version."

    def test_hub_not_configured_pure_filesystem(self):
        reg = PromptRegistry()
        reg.register("tpl", "{{ value }}")
        result = reg.render("tpl", value="pure-fs")
        assert result == "pure-fs"
        info = reg.get_template_version_info("tpl")
        assert "hash" in info
        assert info["hash"] is not None
        assert len(info["hash"]) == 64
        assert "source" not in info
