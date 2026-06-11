"""Tests for infra/providers.py ProviderRegistry."""
from __future__ import annotations


class TestProviderRegistry:
    def test_registry_initializes_with_builtin_providers(self):
        from general_ludd.infra.providers import ProviderRegistry
        registry = ProviderRegistry()
        assert registry is not None

    def test_registry_has_aws_provider(self):
        from general_ludd.infra.providers import ComputeProvider, ProviderRegistry
        registry = ProviderRegistry()
        info = registry.get(ComputeProvider.AWS)
        assert info is not None
        assert info.display_name == "Amazon Web Services"
