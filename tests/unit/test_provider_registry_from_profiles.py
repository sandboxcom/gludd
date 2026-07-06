"""Tests for ProviderRegistry.from_profiles factory.

Every gateway built in src/ currently passes ``provider_registry=None``, so
live model calls raise "No provider registry configured". The
``ProviderRegistry.from_profiles`` factory builds a populated registry from
model profiles so the wiring (daemon.py / worker app / models.py) can hand a
real registry to the gateway.
"""

from __future__ import annotations

import importlib.util

from general_ludd.models.provider_registry import ProviderInfo, ProviderRegistry


def _zai_style_dict() -> dict:
    """A zai_example.yml-shaped profile dict (provider: openai)."""
    return {
        "model_profile_id": "zai_coder",
        "provider": "openai",
        "provider_package": "langchain_openai",
        "provider_class_hint": "ChatOpenAI",
        "model_name": "glm-4.6",
    }


class _FakeProfile:
    """Stand-in for a ModelProfile object (attribute access, not dict)."""

    def __init__(self, provider="openai", package="langchain_openai", hint="ChatOpenAI"):
        self.provider = provider
        self.provider_package = package
        self.provider_class_hint = hint


def test_from_profiles_registers_openai_from_dict_profile():
    registry = ProviderRegistry.from_profiles([_zai_style_dict()])

    info = registry.get_provider_info("openai")
    assert isinstance(info, ProviderInfo)
    # Import name (underscore), never the pip distribution name.
    assert info.package_name == "langchain_openai"
    assert info.class_hint == "ChatOpenAI"
    assert "openai" in registry.list_providers()


def test_from_profiles_registers_from_modelprofile_object():
    registry = ProviderRegistry.from_profiles([_FakeProfile()])

    info = registry.get_provider_info("openai")
    assert info is not None
    assert info.package_name == "langchain_openai"
    assert info.class_hint == "ChatOpenAI"


def test_from_profiles_resolves_chatopenai_when_installed():
    """If langchain_openai is installed, get_provider_class resolves the class.

    When it is not installed, registration still succeeds and is_installed
    reports False (the registry then emits a dependency todo rather than
    crashing).
    """
    registry = ProviderRegistry.from_profiles([_zai_style_dict()])

    if importlib.util.find_spec("langchain_openai") is not None:
        assert registry.is_installed("openai") is True
        cls = registry.get_provider_class("openai")
        assert cls.__name__ == "ChatOpenAI"
    else:
        # Not installed: registration succeeded but the package is absent.
        assert registry.is_installed("openai") is False
        assert registry.get_provider_info("openai") is not None


def test_from_profiles_dedupes_providers_sharing_a_name():
    profiles = [
        _zai_style_dict(),
        {"provider": "openai", "provider_package": "langchain_openai"},
        _FakeProfile(provider="openai"),
    ]
    registry = ProviderRegistry.from_profiles(profiles)

    # Presets are registered first; "openai" appears exactly once.
    assert registry.list_providers().count("openai") == 1
    # Preset metadata wins for stability (langchain_openai + ChatOpenAI).
    info = registry.get_provider_info("openai")
    assert info is not None
    assert info.package_name == "langchain_openai"
    assert info.class_hint == "ChatOpenAI"


def test_from_profiles_registers_distinct_providers():
    profiles = [
        _zai_style_dict(),
        {
            "provider": "anthropic",
            "provider_package": "langchain_anthropic",
            "provider_class_hint": "ChatAnthropic",
        },
    ]
    registry = ProviderRegistry.from_profiles(profiles)

    # Both profile-referenced providers are present (plus presets).
    assert {"openai", "anthropic"}.issubset(set(registry.list_providers()))
    assert registry.get_provider_info("anthropic").package_name == "langchain_anthropic"


def test_from_profiles_normalizes_pip_name_to_import_name():
    """A hyphenated pip name (langchain-openai) is normalized to the import name."""
    profile = {"provider": "openai", "provider_package": "langchain-openai"}
    registry = ProviderRegistry.from_profiles([profile])

    assert registry.get_provider_info("openai").package_name == "langchain_openai"


def test_from_profiles_applies_defaults_for_missing_fields():
    """A profile missing package/hint gets langchain_openai + ChatOpenAI defaults."""
    registry = ProviderRegistry.from_profiles([{"provider": "openai"}])

    info = registry.get_provider_info("openai")
    assert info.package_name == "langchain_openai"
    assert info.class_hint == "ChatOpenAI"


def test_from_profiles_defaults_provider_name_to_openai():
    """A profile with no provider field defaults the provider name to openai."""
    registry = ProviderRegistry.from_profiles([{"model_profile_id": "x"}])

    assert "openai" in registry.list_providers()


def test_from_profiles_handles_empty_list():
    """Empty profile list still yields a presets-populated registry."""
    from general_ludd.models.provider_presets import PROVIDER_PRESETS

    registry = ProviderRegistry.from_profiles([])

    assert set(registry.list_providers()) == set(PROVIDER_PRESETS.keys())


def test_from_profiles_handles_none():
    """None profile list still yields a presets-populated registry."""
    from general_ludd.models.provider_presets import PROVIDER_PRESETS

    registry = ProviderRegistry.from_profiles(None)

    assert set(registry.list_providers()) == set(PROVIDER_PRESETS.keys())
