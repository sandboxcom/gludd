"""CI-1 regression: gateway built via ProviderRegistry.from_profiles resolves a provider.

The CI-1 fix wired ``ProviderRegistry.from_profiles(profiles)`` into the
daemon/worker gateway construction. Previously those gateways were built with
``provider_registry=None`` so every live model call raised
``"No provider registry configured"`` (see ModelGateway._invoke_and_bill, which
raises a ValueError with that text when ``self._registry is None``).

These tests pin three properties of the fix:

1. A ProviderRegistry built from a zai-style profile resolves the ``openai``
   provider WITHOUT raising "not registered" — and, in this venv where
   ``langchain_openai`` is installed, ``get_provider_class("openai")`` returns
   the real ChatOpenAI class.
2. A ModelGateway built with ``provider_registry=ProviderRegistry.from_profiles(...)``
   has a non-None ``_registry`` (the exact regression) and, with a mocked
   provider class + stub secrets, ``call_model`` reaches the provider instead of
   raising "No provider registry configured".
3. NEGATIVE CONTROL: a ModelGateway built with ``provider_registry=None`` still
   raises "No provider registry configured" on call — documenting the bug the
   fix closes.
"""

from __future__ import annotations

import importlib.util
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from general_ludd.models.gateway import ModelGateway, ModelProfile
from general_ludd.models.provider_registry import ProviderRegistry

# A zai_coder-shaped profile dict (mirrors config/model_profiles/zai_example.yml).
ZAI_PROFILE_DICT: dict[str, Any] = {
    "model_profile_id": "zai_coder",
    "provider": "openai",
    "provider_package": "langchain_openai",
    "provider_class_hint": "ChatOpenAI",
    "model_name": "glm-4.6",
    "credential_alias": "ZAI_API_KEY",
    "api_base_alias": "ZAI_BASE_URL",
    "enabled": True,
}

_LANGCHAIN_OPENAI_INSTALLED = importlib.util.find_spec("langchain_openai") is not None


def _zai_model_profile() -> ModelProfile:
    """A ModelProfile matching the zai_coder profile shape."""
    return ModelProfile(
        model_profile_id="zai_coder",
        provider="openai",
        provider_package="langchain_openai",
        provider_class_hint="ChatOpenAI",
        model_name="glm-4.6",
        credential_alias="ZAI_API_KEY",
        api_base_alias="ZAI_BASE_URL",
        enabled=True,
    )


class _StubSecrets:
    """Minimal _SecretsResolver: resolves any alias to a fixed dummy value."""

    def resolve(self, alias_name: str) -> str | None:
        if alias_name == "ZAI_BASE_URL":
            # A safe (loopback-free, public-shaped) base URL the SSRF guard
            # accepts; never actually contacted because the provider is mocked.
            return "https://api.z.ai/api/coding/paas/v4"
        return "dummy-key"


# ---------------------------------------------------------------------------
# 2a. ProviderRegistry.from_profiles resolves the provider without raising.
# ---------------------------------------------------------------------------


def test_from_profiles_registers_openai_provider() -> None:
    """from_profiles populates the registry with the profile's provider."""
    registry = ProviderRegistry.from_profiles([ZAI_PROFILE_DICT])

    assert registry.get_provider_info("openai") is not None
    assert "openai" in registry.list_providers()
    info = registry.get_provider_info("openai")
    assert info is not None
    assert info.package_name == "langchain_openai"
    assert info.class_hint == "ChatOpenAI"


def test_from_profiles_accepts_model_profile_objects() -> None:
    """from_profiles works on ModelProfile objects too, not only dicts."""
    registry = ProviderRegistry.from_profiles([_zai_model_profile()])
    info = registry.get_provider_info("openai")
    assert info is not None
    # ModelProfile defaults provider_package to the pip name 'langchain-openai';
    # from_profiles normalizes hyphens -> underscores to the IMPORT name.
    assert info.package_name == "langchain_openai"


def test_get_provider_class_does_not_raise_not_registered() -> None:
    """get_provider_class('openai') never raises the 'not registered' ValueError.

    When langchain_openai is installed (it is in this venv) it resolves to the
    real ChatOpenAI class. If it is NOT installed in some environment, it raises
    ImportError (package not installed) — never the 'not registered' ValueError
    that would mean from_profiles failed to register the provider. We assert the
    registration succeeded in either case.
    """
    registry = ProviderRegistry.from_profiles([ZAI_PROFILE_DICT])

    if _LANGCHAIN_OPENAI_INSTALLED:
        cls = registry.get_provider_class("openai")
        assert cls is not None
        assert cls.__name__ == "ChatOpenAI"
    else:  # pragma: no cover - langchain_openai is present in this venv
        with pytest.raises(ImportError) as excinfo:
            registry.get_provider_class("openai")
        # Must be the "not installed" path, NOT "not registered".
        assert "not registered" not in str(excinfo.value)
        assert "not installed" in str(excinfo.value)
        # And registration genuinely happened:
        assert registry.get_provider_info("openai") is not None


# ---------------------------------------------------------------------------
# 2b. Gateway built via from_profiles has a non-None _registry and reaches
#     the provider on call_model.
# ---------------------------------------------------------------------------


def test_gateway_built_with_from_profiles_has_non_none_registry() -> None:
    """THE REGRESSION: _registry is non-None when wired via from_profiles.

    Pre-fix the daemon/worker built ModelGateway(provider_registry=None), so
    _registry was None and call_model raised 'No provider registry configured'.
    """
    profile = _zai_model_profile()
    gateway = ModelGateway(
        profiles=[profile],
        provider_registry=ProviderRegistry.from_profiles([profile]),
        secrets_manager=_StubSecrets(),
    )
    assert gateway._registry is not None
    assert "openai" in gateway._registry.list_providers()


def test_gateway_call_model_reaches_provider_not_no_registry_error() -> None:
    """With a non-None registry, call_model reaches the mocked provider.

    We mock the provider class so no network call happens: the provider returns
    a fake AIMessage-like response. The key assertion is that call_model does
    NOT raise 'No provider registry configured' and that the provider class is
    actually invoked.
    """
    profile = _zai_model_profile()
    registry = ProviderRegistry.from_profiles([profile])

    # Stub the provider class so is_installed() is True and get_provider_class()
    # returns our mock instead of the real ChatOpenAI (avoids any network call).
    fake_response = MagicMock()
    fake_response.content = "hello from mocked provider"
    fake_response.usage_metadata = {"input_tokens": 3, "output_tokens": 5}
    fake_response.tool_calls = None

    chat_instance = MagicMock()
    chat_instance.invoke.return_value = fake_response

    provider_cls = MagicMock(return_value=chat_instance)

    cast(Any, registry).is_installed = MagicMock(return_value=True)
    cast(Any, registry).get_provider_class = MagicMock(return_value=provider_cls)

    gateway = ModelGateway(
        profiles=[profile],
        provider_registry=registry,
        secrets_manager=_StubSecrets(),
    )

    response = gateway.call_model("zai_coder", [{"role": "user", "content": "hi"}])

    assert response.content == "hello from mocked provider"
    # Provider class was constructed and invoked — we reached the provider.
    provider_cls.assert_called_once()
    chat_instance.invoke.assert_called_once()


# ---------------------------------------------------------------------------
# 2c. Negative control: provider_registry=None still raises the documented bug.
# ---------------------------------------------------------------------------


def test_gateway_with_none_registry_raises_no_provider_registry_configured() -> None:
    """NEGATIVE CONTROL: the bug the CI-1 fix closes.

    A gateway built the old way (provider_registry=None) still raises
    'No provider registry configured' on call_model — proving the regression
    test above is meaningful and the error path is unchanged for the None case.
    """
    profile = _zai_model_profile()
    gateway = ModelGateway(
        profiles=[profile],
        provider_registry=None,
        secrets_manager=_StubSecrets(),
    )
    assert gateway._registry is None

    with pytest.raises(ValueError) as excinfo:
        gateway.call_model("zai_coder", [{"role": "user", "content": "hi"}])

    assert "No provider registry configured" in str(excinfo.value)
