"""Tests for the provider-package allowlist that blocks arbitrary-import RCE.

These tests verify that provider_registry.py and ModelProfile enforce a
hardcoded allowlist of LangChain provider packages BEFORE any importlib call,
so that a malicious config profile such as:

    provider_package: os
    provider_class_hint: system

cannot be used to execute arbitrary OS commands.
"""

from __future__ import annotations

from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.models.gateway import ModelProfile
from general_ludd.models.provider_registry import (
    PROVIDER_PACKAGE_ALLOWLIST,
    ProviderRegistry,
)


class TestAllowlistContents:
    """Smoke-check the allowlist itself contains the expected entries."""

    def test_langchain_openai_in_allowlist(self) -> None:
        assert "langchain-openai" in PROVIDER_PACKAGE_ALLOWLIST

    def test_langchain_anthropic_in_allowlist(self) -> None:
        assert "langchain-anthropic" in PROVIDER_PACKAGE_ALLOWLIST

    def test_os_not_in_allowlist(self) -> None:
        assert "os" not in PROVIDER_PACKAGE_ALLOWLIST

    def test_builtins_not_in_allowlist(self) -> None:
        assert "builtins" not in PROVIDER_PACKAGE_ALLOWLIST

    def test_subprocess_not_in_allowlist(self) -> None:
        assert "subprocess" not in PROVIDER_PACKAGE_ALLOWLIST


class TestRegisterProviderAllowlist:
    """register_provider rejects non-allowlisted packages before any import."""

    def test_os_package_rejected_on_register(self) -> None:
        reg = ProviderRegistry()
        with pytest.raises(ValueError, match="not in the allowlist"):
            reg.register_provider("evil", "os", "system")
        # The provider must NOT be registered at all.
        assert reg.get_provider_info("evil") is None

    def test_subprocess_package_rejected_on_register(self) -> None:
        reg = ProviderRegistry()
        with pytest.raises(ValueError, match="not in the allowlist"):
            reg.register_provider("evil", "subprocess", "check_output")

    def test_arbitrary_package_rejected_on_register(self) -> None:
        reg = ProviderRegistry()
        with pytest.raises(ValueError, match="not in the allowlist"):
            reg.register_provider("evil", "totally-fake-provider-xyz", "SomeClass")

    def test_langchain_openai_accepted_on_register(self) -> None:
        reg = ProviderRegistry()
        # Should not raise.
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")
        info = reg.get_provider_info("openai")
        assert info is not None
        assert info.package_name == "langchain-openai"

    def test_underscore_variant_accepted(self) -> None:
        """langchain_openai (underscores) is normalised to langchain-openai."""
        reg = ProviderRegistry()
        reg.register_provider("openai2", "langchain_openai", "ChatOpenAI")
        info = reg.get_provider_info("openai2")
        assert info is not None

    def test_no_import_attempted_on_reject(self) -> None:
        """ValueError is raised synchronously before any is_installed/import check.

        We verify this by confirming the provider is never registered (so
        is_installed, which calls find_spec, is never reached) and that
        get_provider_info returns None (no side-effects from the rejected call).
        """
        reg = ProviderRegistry()
        with pytest.raises(ValueError, match="not in the allowlist"):
            reg.register_provider("evil", "os", "system")
        # is_installed calls find_spec; if the package were registered and
        # find_spec were called, is_installed might return True for "os".
        # Confirming get_provider_info is None proves no partial side-effect.
        assert reg.get_provider_info("evil") is None
        # list_providers must not include the rejected name.
        assert "evil" not in reg.list_providers()


class TestGetProviderClassAllowlist:
    """get_provider_class re-validates before import (defence-in-depth)."""

    def test_os_package_rejected_before_import(self) -> None:
        """Even if ProviderInfo is constructed directly, import is blocked."""
        from general_ludd.models.provider_registry import ProviderInfo

        reg = ProviderRegistry()
        # Bypass register_provider by inserting directly (simulates a future
        # persistence layer that deserialises untrusted data without validation).
        reg._providers["evil"] = ProviderInfo(
            name="evil", package_name="os", class_hint="system"
        )
        with patch("importlib.import_module") as mock_import:
            with pytest.raises(ValueError, match="not in the allowlist"):
                reg.get_provider_class("evil")
            mock_import.assert_not_called()

    def test_langchain_openai_class_returned(self) -> None:
        """A fully allowlisted + installed package returns the class."""
        fake_module = ModuleType("langchain_openai")
        FakeChat = type("ChatOpenAI", (), {})
        fake_module.ChatOpenAI = FakeChat  # type: ignore[attr-defined]

        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")

        with (
            patch("importlib.util.find_spec", return_value=MagicMock()),
            patch("importlib.import_module", return_value=fake_module),
        ):
            cls = reg.get_provider_class("openai")
        assert cls is FakeChat

    def test_non_class_hint_rejected(self) -> None:
        """getattr result that is not a class raises TypeError."""
        fake_module = ModuleType("langchain_openai")
        fake_module.not_a_class = "just a string"  # type: ignore[attr-defined]

        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "not_a_class")

        with (
            patch("importlib.util.find_spec", return_value=MagicMock()),
            patch("importlib.import_module", return_value=fake_module),
            pytest.raises(TypeError, match="non-class"),
        ):
            reg.get_provider_class("openai")


class TestModelProfileAllowlist:
    """ModelProfile.provider_package field_validator enforces the allowlist."""

    def test_langchain_openai_accepted(self) -> None:
        profile = ModelProfile(
            model_profile_id="test-profile",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
        )
        assert profile.provider_package == "langchain-openai"

    def test_os_package_rejected_by_model_profile(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="not in the allowlist"):
            ModelProfile(
                model_profile_id="evil-profile",
                provider_package="os",
                provider_class_hint="system",
            )

    def test_arbitrary_package_rejected_by_model_profile(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="not in the allowlist"):
            ModelProfile(
                model_profile_id="evil-profile",
                provider_package="totally-fake-provider-xyz",
                provider_class_hint="SomeClass",
            )

    def test_langchain_anthropic_accepted(self) -> None:
        profile = ModelProfile(
            model_profile_id="anthropic-profile",
            provider_package="langchain-anthropic",
            provider_class_hint="ChatAnthropic",
        )
        assert profile.provider_package == "langchain-anthropic"

    def test_default_provider_package_is_allowlisted(self) -> None:
        """The default value (langchain-openai) must itself pass validation."""
        profile = ModelProfile(model_profile_id="default-profile")
        assert profile.provider_package in PROVIDER_PACKAGE_ALLOWLIST
