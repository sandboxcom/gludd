"""C6: Model gateway hardening — SSRF caller-kwargs override, timeouts, leak.

Three issues fixed:
1. Caller kwargs can override SSRF-validated base_url/api_key
2. No request/connect timeout
3. Alias-resolved URL leaked in SSRF error text
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from general_ludd.models.gateway import (
    ModelGateway,
    ModelProfile,
    SSRFRejectionError,
)
from general_ludd.models.provider_registry import ProviderRegistry


def _capture_provider(recorder: dict):
    """Provider class stand-in that records the ctor kwargs into ``recorder``."""

    def factory(**kwargs):
        recorder.clear()
        recorder.update(kwargs)
        instance = MagicMock()
        instance.invoke.return_value = MagicMock(
            content="ok",
            usage_metadata={"input_tokens": 1, "output_tokens": 1},
            tool_calls=[],
        )
        instance.bind_tools.return_value = instance
        return instance

    return factory


class _DictSecretsResolver:
    """Secrets resolver that maps alias names to values from a dict."""

    def __init__(self, values: dict[str, str]):
        self._values = values

    def resolve(self, alias_name: str) -> str | None:
        return self._values.get(alias_name)


class TestCallerKwargsCannotOverrideValidatedBaseUrl:
    """C6-1: caller-supplied base_url in kwargs must NEVER override an
    SSRF-validated profile base_url (or reach the provider without validation)."""

    def _make_gateway(self):
        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")
        secrets = _DictSecretsResolver({
            "openai_key": "sk-alias-key",
            "openai_base": "https://safe-api.example.com",
        })
        profile = ModelProfile(
            model_profile_id="gpt4",
            enabled=True,
            provider="openai",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="gpt-4",
            api_metered=False,
            credential_alias="openai_key",
            api_base_alias="openai_base",
            run_budget_usd=100.0,
        )
        gw = ModelGateway(
            profiles=[profile],
            provider_registry=reg,
            secrets_manager=secrets,
        )
        return gw, reg

    def test_caller_kwargs_cannot_override_validated_base_url(self):
        """A caller-supplied base_url in kwargs must NOT override the alias-
        resolved, SSRF-validated base_url on the profile."""
        gw, reg = self._make_gateway()
        captured: dict = {}
        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=_capture_provider(captured)),
        ):
            resp = gw.call_model(
                "gpt4",
                [{"role": "user", "content": "hi"}],
                base_url="https://evil-proxy.example.com",
            )
        assert isinstance(resp, object)
        assert resp.content == "ok"
        assert "base_url" not in captured or captured.get("base_url") != "https://evil-proxy.example.com"

    def test_caller_kwargs_cannot_override_validated_api_key(self):
        """A caller-supplied api_key in kwargs must NOT override the alias-
        resolved credential on the profile."""
        gw, reg = self._make_gateway()
        captured: dict = {}
        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=_capture_provider(captured)),
        ):
            resp = gw.call_model(
                "gpt4",
                [{"role": "user", "content": "hi"}],
                api_key="caller-injected-evil-key",  # pragma: allowlist secret
            )
        assert isinstance(resp, object)
        assert resp.content == "ok"
        assert (
            "api_key" not in captured
            or captured.get("api_key") != "caller-injected-evil-key"
        )  # pragma: allowlist secret


class TestGatewayHasRequestTimeout:
    """C6-2: the gateway must configure a request+connect timeout on the httpx
    client used by the provider."""

    def _make_gateway(self):
        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")
        profile = ModelProfile(
            model_profile_id="gpt4_to",
            enabled=True,
            provider="openai",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="gpt-4",
            api_metered=False,
            run_budget_usd=100.0,
        )
        gw = ModelGateway(profiles=[profile], provider_registry=reg)
        return gw, reg

    def test_gateway_has_request_timeout(self):
        """The provider constructor must receive a timeout configuration
        with a connect timeout."""
        gw, reg = self._make_gateway()
        captured: dict = {}
        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=_capture_provider(captured)),
        ):
            resp = gw.call_model(
                "gpt4_to",
                [{"role": "user", "content": "hi"}],
            )
        assert isinstance(resp, object)
        assert resp.content == "ok"

        timeout = captured.get("request_timeout")
        assert timeout is not None, (
            f"request_timeout not set in provider kwargs; got {list(captured.keys())}"
        )
        if isinstance(timeout, httpx.Timeout):
            assert timeout.connect is not None, "httpx.Timeout has no connect timeout"
        elif isinstance(timeout, (int, float)):
            pass
        else:
            assert isinstance(timeout, (int, float, httpx.Timeout)), (
                f"unexpected timeout type: {type(timeout)}"
            )


class TestSSrfErrorDoesNotLeakResolvedUrl:
    """C6-3: SSRF rejection errors must NOT leak the resolved/validated URL
    or IP address in the error text."""

    def _make_gateway_with_blocked_alias(self):
        reg = ProviderRegistry()
        reg.register_provider("openai", "langchain-openai", "ChatOpenAI")
        secrets = _DictSecretsResolver({
            "ssrf_alias": "http://169.254.169.254/latest/meta-data/",
        })
        profile = ModelProfile(
            model_profile_id="ssrf_blocked",
            enabled=True,
            provider="openai",
            provider_package="langchain-openai",
            provider_class_hint="ChatOpenAI",
            model_name="gpt-4",
            api_metered=False,
            api_base_alias="ssrf_alias",
            run_budget_usd=100.0,
        )
        gw = ModelGateway(
            profiles=[profile],
            provider_registry=reg,
            secrets_manager=secrets,
        )
        return gw, reg

    def test_ssrf_error_does_not_leak_resolved_url(self):
        """When an api_base_alias resolves to a blocked URL, the SSRF error
        must NOT contain the raw resolved URL or IP."""
        gw, reg = self._make_gateway_with_blocked_alias()
        FakeChatModel = MagicMock()
        with (
            patch.object(reg, "is_installed", return_value=True),
            patch.object(reg, "get_provider_class", return_value=FakeChatModel),
            pytest.raises(SSRFRejectionError) as exc_info,
        ):
            gw.call_model(
                "ssrf_blocked",
                [{"role": "user", "content": "hi"}],
            )

        error_msg = str(exc_info.value)
        assert "169.254.169.254" not in error_msg, (
            f"SSRF error leaked blocked IP: {error_msg!r}"
        )
