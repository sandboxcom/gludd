"""Unit tests for provider registry."""

from __future__ import annotations

from types import ModuleType
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.models.provider_registry import ProviderInfo, ProviderRegistry


class TestProviderRegistryRegister:
    def test_register_provider_stores_mapping(self) -> None:
        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")
        info = reg.get_provider_info("openai")
        assert info is not None
        assert info.package_name == "langchain_openai"
        assert info.class_hint == "ChatOpenAI"

    def test_register_multiple_providers(self) -> None:
        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")
        reg.register_provider("anthropic", "langchain-anthropic", "ChatAnthropic")
        assert len(reg.list_providers()) == 2


class TestProviderRegistryCheckInstalled:
    def test_installed_provider_detected(self) -> None:
        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")
        with patch("importlib.util.find_spec", return_value=MagicMock()):
            assert reg.is_installed("openai") is True

    def test_missing_provider_not_installed(self) -> None:
        reg = ProviderRegistry()
        reg.register_provider(
            "huggingface", "langchain-huggingface", "HuggingFaceEndpoint"
        )
        with patch("importlib.util.find_spec", return_value=None):
            assert reg.is_installed("huggingface") is False

    def test_unknown_provider_not_installed(self) -> None:
        reg = ProviderRegistry()
        assert reg.is_installed("unknown_provider") is False


class TestProviderRegistryDepUpdateTodo:
    def test_creates_dep_update_todo_for_missing(self) -> None:
        reg = ProviderRegistry()
        reg.register_provider(
            "huggingface", "langchain-huggingface", "HuggingFaceEndpoint"
        )
        with patch("importlib.util.find_spec", return_value=None):
            todo = reg.install_provider("huggingface")
        assert todo is not None
        assert todo.work_type.value == "dependency"
        assert "langchain_huggingface" in todo.title

    def test_install_returns_none_if_already_installed(self) -> None:
        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")
        with patch("importlib.util.find_spec", return_value=MagicMock()):
            todo = reg.install_provider("openai")
        assert todo is None


class TestProviderRegistryDynamicImport:
    def test_dynamic_import_returns_class(self) -> None:
        fake_module = ModuleType("langchain_openai")
        FakeClass = type("FakeChatModel", (), {"__init__": lambda self: None})
        cast(Any, fake_module).ChatOpenAI = FakeClass

        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")

        with (
            patch("importlib.util.find_spec", return_value=MagicMock()),
            patch("importlib.import_module", return_value=fake_module),
        ):
            cls = reg.get_provider_class("openai")
            assert cls is FakeClass

    def test_dynamic_import_raises_for_missing_provider(self) -> None:
        reg = ProviderRegistry()
        with pytest.raises(ValueError, match="not registered"):
            reg.get_provider_class("nonexistent")

    def test_dynamic_import_raises_for_uninstalled_provider(self) -> None:
        reg = ProviderRegistry()
        reg.register_provider(
            "huggingface", "langchain-huggingface", "HuggingFaceEndpoint"
        )
        with (
            patch("importlib.util.find_spec", return_value=None),
            pytest.raises(ImportError, match="not installed"),
        ):
            reg.get_provider_class("huggingface")


class TestProviderRegistryImportPolicy:
    def test_registration_rejects_unapproved_target_without_state_change(self) -> None:
        reg = ProviderRegistry()

        with pytest.raises(ValueError, match="approved provider import target"):
            reg.register_provider("evil", "os", "system")

        assert reg.get_provider_info("evil") is None

    def test_registration_rejects_wrong_class_in_approved_package(self) -> None:
        reg = ProviderRegistry()

        with pytest.raises(ValueError, match="approved provider import target"):
            reg.register_provider("evil", "langchain-openai", "system")

    def test_tampered_registry_is_rejected_before_import_discovery(self) -> None:
        reg = ProviderRegistry()
        reg._providers["evil"] = ProviderInfo("evil", "os", "system")

        with (
            patch("importlib.util.find_spec") as find_spec,
            patch("importlib.import_module") as import_module,
            pytest.raises(ValueError, match="approved provider import target"),
        ):
            reg.get_provider_class("evil")

        find_spec.assert_not_called()
        import_module.assert_not_called()

    def test_approved_target_must_resolve_to_a_class(self) -> None:
        fake_module = ModuleType("langchain_openai")
        cast(Any, fake_module).ChatOpenAI = "not-a-class"
        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")

        with (
            patch("importlib.util.find_spec", return_value=MagicMock()),
            patch("importlib.import_module", return_value=fake_module),
            pytest.raises(TypeError, match="must resolve to a class"),
        ):
            reg.get_provider_class("openai")


class TestProviderRegistryListProviders:
    def test_list_providers_returns_all(self) -> None:
        reg = ProviderRegistry()
        reg.register_provider("a", "langchain-openai", "ChatOpenAI")
        reg.register_provider("b", "langchain-anthropic", "ChatAnthropic")
        names = reg.list_providers()
        assert "a" in names
        assert "b" in names
