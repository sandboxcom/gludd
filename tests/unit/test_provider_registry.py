"""Unit tests for provider registry."""

from __future__ import annotations

from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from agentic_harness.models.provider_registry import ProviderRegistry


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
        reg.register_provider("openrouter", "langchain-openrouter", "ChatOpenRouter")
        assert len(reg.list_providers()) == 2


class TestProviderRegistryCheckInstalled:
    def test_installed_provider_detected(self):
        reg = ProviderRegistry()
        reg.register_provider("pydantic_pkg", "pydantic", "BaseModel")
        assert reg.is_installed("pydantic_pkg") is True

    def test_missing_provider_not_installed(self):
        reg = ProviderRegistry()
        reg.register_provider("fake", "nonexistent-package-xyz", "FakeClass")
        assert reg.is_installed("fake") is False

    def test_unknown_provider_not_installed(self):
        reg = ProviderRegistry()
        assert reg.is_installed("unknown_provider") is False


class TestProviderRegistryDepUpdateTodo:
    def test_creates_dep_update_todo_for_missing(self):
        reg = ProviderRegistry()
        reg.register_provider("fake", "nonexistent-package-xyz", "FakeClass")
        todo = reg.install_provider("fake")
        assert todo is not None
        assert todo.work_type.value == "dependency"
        assert "nonexistent-package-xyz" in todo.title

    def test_install_returns_none_if_already_installed(self):
        reg = ProviderRegistry()
        reg.register_provider("pydantic_pkg", "pydantic", "BaseModel")
        todo = reg.install_provider("pydantic_pkg")
        assert todo is None


class TestProviderRegistryDynamicImport:
    def test_dynamic_import_returns_class(self):
        fake_module = ModuleType("fake_provider_mod")
        FakeClass = type("FakeChatModel", (), {"__init__": lambda self: None})
        fake_module.FakeChatModel = FakeClass  # type: ignore[attr-defined]

        reg = ProviderRegistry()
        reg.register_provider("fake", "fake_provider_mod", "FakeChatModel")

        with (
            patch("importlib.util.find_spec", return_value=MagicMock()),
            patch("importlib.import_module", return_value=fake_module),
        ):
            cls = reg.get_provider_class("fake")
            assert cls is FakeClass

    def test_dynamic_import_raises_for_missing_provider(self):
        reg = ProviderRegistry()
        with pytest.raises(ValueError, match="not registered"):
            reg.get_provider_class("nonexistent")

    def test_dynamic_import_raises_for_uninstalled_provider(self):
        reg = ProviderRegistry()
        reg.register_provider("fake", "nonexistent-package-xyz", "FakeClass")
        with pytest.raises(ImportError, match="not installed"):
            reg.get_provider_class("fake")


class TestProviderRegistryListProviders:
    def test_list_providers_returns_all(self):
        reg = ProviderRegistry()
        reg.register_provider("a", "pkg_a", "ClsA")
        reg.register_provider("b", "pkg_b", "ClsB")
        names = reg.list_providers()
        assert "a" in names
        assert "b" in names
