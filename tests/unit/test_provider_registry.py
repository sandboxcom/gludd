"""Unit tests for provider registry."""

from __future__ import annotations

from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.models.provider_registry import ProviderRegistry


class TestProviderRegistryRegister:
    def test_register_provider_stores_mapping(self):
        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")
        info = reg.get_provider_info("openai")
        assert info is not None
        assert info.package_name == "langchain-openai"
        assert info.class_hint == "ChatOpenAI"

    def test_register_multiple_providers(self):
        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")
        reg.register_provider("anthropic", "langchain-anthropic", "ChatAnthropic")
        assert len(reg.list_providers()) == 2


class TestProviderRegistryCheckInstalled:
    def test_installed_provider_detected(self):
        reg = ProviderRegistry()
        # langchain-openai is in pyproject.toml and will be installed in the venv.
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")
        assert reg.is_installed("openai") is True

    def test_missing_provider_not_installed(self):
        reg = ProviderRegistry()
        # langchain-groq is allowlisted but not installed in the test venv.
        reg.register_provider("groq", "langchain-groq", "ChatGroq")
        assert reg.is_installed("groq") is False

    def test_unknown_provider_not_installed(self):
        reg = ProviderRegistry()
        assert reg.is_installed("unknown_provider") is False


class TestProviderRegistryDepUpdateTodo:
    def test_creates_dep_update_todo_for_missing(self):
        reg = ProviderRegistry()
        # langchain-groq is allowlisted but not installed in the test venv.
        reg.register_provider("groq", "langchain-groq", "ChatGroq")
        todo = reg.install_provider("groq")
        assert todo is not None
        assert todo.work_type.value == "dependency"
        assert "langchain-groq" in todo.title

    def test_install_returns_none_if_already_installed(self):
        reg = ProviderRegistry()
        # langchain-openai IS installed in the test venv.
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")
        todo = reg.install_provider("openai")
        assert todo is None


class TestProviderRegistryDynamicImport:
    def test_dynamic_import_returns_class(self):
        fake_module = ModuleType("langchain_openai")
        FakeClass = type("ChatOpenAI", (), {"__init__": lambda self: None})
        fake_module.ChatOpenAI = FakeClass  # type: ignore[attr-defined]

        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")

        with (
            patch("importlib.util.find_spec", return_value=MagicMock()),
            patch("importlib.import_module", return_value=fake_module),
        ):
            cls = reg.get_provider_class("openai")
            assert cls is FakeClass

    def test_dynamic_import_raises_for_missing_provider(self):
        reg = ProviderRegistry()
        with pytest.raises(ValueError, match="not registered"):
            reg.get_provider_class("nonexistent")

    def test_dynamic_import_raises_for_uninstalled_provider(self):
        reg = ProviderRegistry()
        # langchain-groq is allowlisted but not installed in the test env.
        reg.register_provider("groq", "langchain-groq", "ChatGroq")
        with pytest.raises(ImportError, match="not installed"):
            reg.get_provider_class("groq")


class TestProviderRegistryListProviders:
    def test_list_providers_returns_all(self):
        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")
        reg.register_provider("anthropic", "langchain-anthropic", "ChatAnthropic")
        names = reg.list_providers()
        assert "openai" in names
        assert "anthropic" in names
