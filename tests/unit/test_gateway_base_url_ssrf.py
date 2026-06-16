"""SSRF guard for the model gateway's config/secrets-supplied base_url.

A caller- or config-supplied ``api_base_alias`` resolves (via the secrets
manager) to a ``base_url`` that the gateway passes straight to the provider
client, which then makes outbound HTTP requests. Without SSRF validation a
malicious / misconfigured alias could point the gateway at a loopback,
private/link-local, or cloud-metadata host (classic SSRF -> credential theft
from the instance metadata service).

These tests prove the gateway now validates the literal host of base_url with
the existing :func:`general_ludd.security.auth.is_safe_fetch_url` helper BEFORE
constructing the provider client, fails CLOSED on a blocked host (raising, and
never invoking the provider), and still accepts a normal public https base_url.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_profile(profile_id="ssrf-p", model_name="test", api_base_alias="API_BASE"):
    profile = MagicMock()
    profile.model_profile_id = profile_id
    profile.model_name = model_name
    profile.provider = "openai"
    profile.credential_alias = "OPENAI_API_KEY"
    profile.api_base_alias = api_base_alias
    profile.run_budget_usd = 200.0
    profile.api_metered = False
    profile.cost_per_input_token = 0.000001
    profile.cost_per_output_token = 0.000002
    return profile


def _build_gateway(resolved_base_url: str):
    """ModelGateway whose secrets manager resolves api_base_alias -> the given URL.

    Returns (gateway, mock_chat_model) so the test can assert the provider was
    or was not invoked.
    """
    from general_ludd.models.gateway import ModelGateway

    profile = _make_profile()

    mock_response = MagicMock()
    mock_response.content = "ok"
    mock_response.usage_metadata = {"input_tokens": 1, "output_tokens": 1}

    mock_chat_model = MagicMock()
    mock_chat_model.invoke.return_value = mock_response

    mock_registry = MagicMock()
    mock_registry.get_provider_class.return_value = MagicMock(return_value=mock_chat_model)
    mock_registry.is_installed.return_value = True

    secrets = MagicMock()

    def _resolve(alias):
        if alias == "API_BASE":
            return resolved_base_url
        if alias == "OPENAI_API_KEY":
            return "test-key"
        return None

    secrets.resolve.side_effect = _resolve

    gw = ModelGateway(
        profiles={profile.model_profile_id: profile},
        provider_registry=mock_registry,
        secrets_manager=secrets,
        response_cache=None,
    )
    return gw, mock_chat_model


_BLOCKED_BASE_URLS = [
    "http://127.0.0.1:8000/v1",            # loopback (also non-https)
    "https://127.0.0.1/v1",                # loopback literal
    "https://localhost/v1",                # loopback name
    "https://169.254.169.254/latest/meta-data/",  # cloud metadata
    "https://[::1]/v1",                    # IPv6 loopback
    "https://10.0.0.5/v1",                 # RFC-1918 private
    "https://192.168.1.1/v1",              # RFC-1918 private
    "https://metadata.google.internal/computeMetadata/v1/",  # GCP metadata name
]


@pytest.mark.parametrize("bad_url", _BLOCKED_BASE_URLS)
def test_blocked_base_url_fails_closed(bad_url):
    gw, mock_chat_model = _build_gateway(bad_url)
    with (
        patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}),
        pytest.raises(ValueError),
    ):
        gw.call_model("ssrf-p", [{"role": "user", "content": "hi"}])
    # Fail CLOSED: the provider client must never have been invoked against the
    # internal/metadata host.
    mock_chat_model.invoke.assert_not_called()


def test_public_https_base_url_is_accepted():
    gw, mock_chat_model = _build_gateway("https://api.openai.com/v1")
    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}):
        resp = gw.call_model("ssrf-p", [{"role": "user", "content": "hi"}])
    assert resp.content == "ok"
    mock_chat_model.invoke.assert_called_once()
