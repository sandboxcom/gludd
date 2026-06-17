"""Response-driven SSRF guards on connector pagination.

Both the Okta and Entra sign-in connectors follow a *server-supplied* next-page
URL (Okta via a ``Link: <url>; rel="next"`` header, Entra via
``@odata.nextLink``). A compromised or malicious upstream could point that URL
at an internal metadata endpoint (``169.254.169.254``), loopback
(``127.0.0.1``) or an unrelated third-party host.

These tests prove the connectors re-validate every pagination URL *before*
fetching it: hostile next URLs are rejected (``ValueError``) and the recording
transport confirms the evil URL is **never** requested. A same-host next URL is
still followed normally.
"""

from __future__ import annotations

import pytest

from general_ludd.connectors.entra_signin import EntraSignInSource
from general_ludd.connectors.okta import OktaSource

# Hostile next-page targets that must always be rejected and never fetched.
HOSTILE_URLS = [
    "http://169.254.169.254/latest/meta-data/",
    "https://169.254.169.254/latest/meta-data/",
    "http://127.0.0.1/api/v1/logs",
    "https://localhost/api/v1/logs",
    "http://[::1]/api/v1/logs",
    "https://evil.example.com/api/v1/logs",  # cross-host, public but not pinned
]


# --------------------------------------------------------------------------- #
# Okta — Link: rel="next" header pagination
# --------------------------------------------------------------------------- #
class _OktaResponse:
    def __init__(self, body, link=None):
        self.status_code = 200
        self._body = body
        self.headers = {"Link": f'<{link}>; rel="next"'} if link else {}

    def json(self):
        return self._body


class _RecordingOktaTransport:
    """Records every fetched URL; replays a scripted sequence of responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.fetched_urls: list[str] = []

    def __call__(self, method, url, *, headers=None, params=None, timeout=30.0):
        self.fetched_urls.append(url)
        if self._responses:
            return self._responses.pop(0)
        return _OktaResponse([])


@pytest.fixture
def okta_token(monkeypatch):
    monkeypatch.setenv("OKTA_TOKEN", "secret")
    return "OKTA_TOKEN"


@pytest.mark.parametrize("hostile", HOSTILE_URLS)
def test_okta_rejects_and_never_fetches_hostile_next_url(okta_token, hostile):
    # First page advertises a hostile next link; the connector must refuse it.
    transport = _RecordingOktaTransport(
        [_OktaResponse([{"published": "t", "eventType": "x"}], link=hostile)]
    )
    source = OktaSource(
        {"org_url": "https://example.okta.com", "token_env": okta_token},
        transport=transport,
    )

    with pytest.raises(ValueError):
        source.query()

    # The hostile URL must NEVER have been requested.
    assert hostile not in transport.fetched_urls
    assert all("169.254.169.254" not in u for u in transport.fetched_urls)
    assert all(
        u.startswith("https://example.okta.com") for u in transport.fetched_urls
    )


def test_okta_follows_same_host_next_url(okta_token):
    same_host_next = "https://example.okta.com/api/v1/logs?after=page2"
    transport = _RecordingOktaTransport(
        [
            _OktaResponse([{"published": "t1"}], link=same_host_next),
            _OktaResponse([{"published": "t2"}]),  # no further link -> stop
        ]
    )
    source = OktaSource(
        {"org_url": "https://example.okta.com", "token_env": okta_token},
        transport=transport,
    )

    out = source.query()

    assert len(out) == 2
    # Both the initial and the same-host next page were fetched.
    assert len(transport.fetched_urls) == 2
    assert transport.fetched_urls[1] == "https://example.okta.com/api/v1/logs"


# --------------------------------------------------------------------------- #
# Entra sign-in — @odata.nextLink pagination
# --------------------------------------------------------------------------- #
class _EntraResponse:
    def __init__(self, value, next_link=None):
        self.status_code = 200
        self._body = {"value": value}
        if next_link is not None:
            self._body["@odata.nextLink"] = next_link

    def json(self):
        return self._body


class _RecordingEntraTransport:
    def __init__(self, responses):
        self._responses = list(responses)
        self.fetched_urls: list[str] = []

    def __call__(self, method, url, *, headers=None, params=None, timeout=30.0):
        self.fetched_urls.append(url)
        if self._responses:
            return self._responses.pop(0)
        return _EntraResponse([])


_ENTRA_BASE = "https://graph.microsoft.com/v1.0/auditLogs/signIns"


@pytest.fixture
def entra_token(monkeypatch):
    monkeypatch.setenv("ENTRA_TOKEN", "secret")
    return "ENTRA_TOKEN"


@pytest.mark.parametrize("hostile", HOSTILE_URLS)
def test_entra_rejects_and_never_fetches_hostile_next_url(entra_token, hostile):
    transport = _RecordingEntraTransport(
        [_EntraResponse([{"createdDateTime": "t"}], next_link=hostile)]
    )
    source = EntraSignInSource(
        {"token_env": entra_token},
        transport=transport,
    )

    with pytest.raises(ValueError):
        source.query()

    assert hostile not in transport.fetched_urls
    assert all("169.254.169.254" not in u for u in transport.fetched_urls)
    assert all(u.startswith(_ENTRA_BASE) for u in transport.fetched_urls)


def test_entra_follows_same_host_next_url(entra_token):
    same_host_next = f"{_ENTRA_BASE}?$skiptoken=page2"
    transport = _RecordingEntraTransport(
        [
            _EntraResponse([{"createdDateTime": "t1"}], next_link=same_host_next),
            _EntraResponse([{"createdDateTime": "t2"}]),  # no nextLink -> stop
        ]
    )
    source = EntraSignInSource(
        {"token_env": entra_token},
        transport=transport,
    )

    out = source.query()

    assert len(out) == 2
    assert len(transport.fetched_urls) == 2
    assert transport.fetched_urls[1] == _ENTRA_BASE
