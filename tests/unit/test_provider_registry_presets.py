"""Tests for ProviderRegistry preset auto-discovery.

``ProviderRegistry.from_presets`` seeds the registry with every entry in
``PROVIDER_PRESETS`` so newly added providers are reachable at startup
without an explicit model profile. ``from_profiles`` layers profile-specific
providers on top of the presets, with presets winning for stability.
"""

from __future__ import annotations

from general_ludd.models.provider_presets import PROVIDER_PRESETS
from general_ludd.models.provider_registry import ProviderInfo, ProviderRegistry

EXPECTED_PRESET_PROVIDERS = [
    "openrouter",
    "openai",
    "anthropic",
    "zai",
    "groq",
    "deepseek",
    "baseten",
    "lambdalabs",
    "together",
    "fireworks",
    "replicate",
    "runpod",
    "modal",
    "coreweave",
    "mistral",
    "cohere",
    "nvidia",
    "perplexity",
    "huggingface",
    "ai21",
    "google",
    "cloudflare",
    "databricks",
    "azure-ai-foundry",
]


def test_from_presets_includes_all_known_providers() -> None:
    registry = ProviderRegistry.from_presets()

    registered = set(registry.list_providers())
    for name in EXPECTED_PRESET_PROVIDERS:
        assert name in registered, f"preset provider {name!r} not registered"

    # The full preset table is covered, not just the documented 24.
    assert registered == set(PROVIDER_PRESETS.keys())


def test_from_presets_has_twenty_four_providers() -> None:
    """The documented provider list has exactly 24 entries."""
    assert len(EXPECTED_PRESET_PROVIDERS) == 24
    assert len(PROVIDER_PRESETS) == 24

    registry = ProviderRegistry.from_presets()
    assert len(registry.list_providers()) == 24


def test_from_profiles_empty_returns_presets_populated_registry() -> None:
    registry = ProviderRegistry.from_profiles([])

    assert set(registry.list_providers()) == set(PROVIDER_PRESETS.keys())


def test_from_profiles_none_returns_presets_populated_registry() -> None:
    registry = ProviderRegistry.from_profiles(None)

    assert set(registry.list_providers()) == set(PROVIDER_PRESETS.keys())


def test_from_profiles_with_baseten_registers_baseten_once() -> None:
    """A profile referencing baseten does not duplicate the preset entry."""
    profile = {
        "provider": "baseten",
        "provider_package": "langchain-openai",
        "provider_class_hint": "ChatOpenAI",
    }
    registry = ProviderRegistry.from_profiles([profile])

    assert registry.list_providers().count("baseten") == 1
    info = registry.get_provider_info("baseten")
    assert isinstance(info, ProviderInfo)


def test_get_provider_info_for_baseten_returns_preset_metadata() -> None:
    registry = ProviderRegistry.from_presets()

    info = registry.get_provider_info("baseten")
    assert info is not None
    # Pip name "langchain-openai" is normalized to the import name.
    assert info.package_name == "langchain_openai"
    assert info.class_hint == "ChatOpenAI"
    assert info.name == "baseten"


def test_from_profiles_does_not_override_preset_metadata() -> None:
    """A profile cannot override the package/class a preset registered."""
    profile = {
        "provider": "baseten",
        "provider_package": "some-other-package",
        "provider_class_hint": "SomeOtherClass",
    }
    registry = ProviderRegistry.from_profiles([profile])

    info = registry.get_provider_info("baseten")
    assert info is not None
    # Preset wins for stability.
    assert info.package_name == "langchain_openai"
    assert info.class_hint == "ChatOpenAI"


def test_from_profiles_adds_provider_absent_from_presets() -> None:
    """The reviewed air-gapped vLLM profile is layered on top of presets."""
    custom = {
        "provider": "vllm",
        "provider_package": "langchain_community",
        "provider_class_hint": "ChatVLLM",
    }
    registry = ProviderRegistry.from_profiles([custom])

    assert "vllm" in registry.list_providers()
    info = registry.get_provider_info("vllm")
    assert info is not None
    assert info.package_name == "langchain_community"
    assert info.class_hint == "ChatVLLM"
    # Presets are still all present.
    assert set(PROVIDER_PRESETS.keys()).issubset(set(registry.list_providers()))
