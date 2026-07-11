"""Unit tests for the self-contained Okta System Log connector.

All HTTP is mocked through an injected fake transport — no network access.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.okta import OktaSource

ORG = "https://example.okta.com"
TOKEN_ENV = "OKTA_TEST_TOKEN"


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._body = body if body is not None else []
        self.headers = headers or {}

    def json(self) -> Any:
        return self._body


class RecordingTransport:
    """Replays a queue of responses and records every call."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> FakeResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "params": params or {},
                "timeout": timeout,
            }
        )
        if not self._responses:
            return FakeResponse(200, [])
        return self._responses.pop(0)


def _raising_transport(*args: Any, **kwargs: Any) -> FakeResponse:
    raise ConnectionError("boom")


CANNED_LOG = [
    {
        "uuid": "evt-1",
        "published": "2026-06-12T10:00:00.000Z",
        "eventType": "user.session.start",
        "displayMessage": "User login to Okta",
        "outcome": {"result": "SUCCESS", "reason": "OK"},
        "actor": {"alternateId": "alice@example.com", "displayName": "Alice"},
        "client": {"ipAddress": "203.0.113.7"},
    },
    {
        "uuid": "evt-2",
        "published": "2026-06-12T10:05:00.000Z",
        "eventType": "user.session.start",
        "displayMessage": "Failed login",
        "outcome": {"result": "FAILURE"},
        "actor": {"alternateId": "bob@example.com"},
        "client": {"ipAddress": "203.0.113.9"},
    },
]


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv(TOKEN_ENV, "s3cr3t-okta-token")
    return "s3cr3t-okta-token"


def _src(transport: Any, **extra: Any) -> OktaSource:
    cfg: dict[str, Any] = {"org_url": ORG, "token_env": TOKEN_ENV}
    cfg.update(extra)
    return OktaSource(cfg, transport=transport)


# -- contract / attributes ------------------------------------------------


def test_class_attrs() -> None:
    assert OktaSource.KIND == "events"
    src = OktaSource(
        {"org_url": ORG, "token_env": TOKEN_ENV}, transport=lambda *a, **k: None
    )
    assert src.name == "okta"


def test_requires_org_url() -> None:
    with pytest.raises(ValueError, match="org_url"):
        OktaSource({"token_env": TOKEN_ENV}, transport=lambda *a, **k: None)


def test_requires_token_env() -> None:
    with pytest.raises(ValueError, match="token_env"):
        OktaSource({"org_url": ORG}, transport=lambda *a, **k: None)


# -- normalization --------------------------------------------------------


def test_query_normalizes_records(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, CANNED_LOG)])
    rows = _src(transport).query({"since": "2026-06-12T00:00:00Z"})

    assert len(rows) == 2
    first = rows[0]
    assert first["ts"] == "2026-06-12T10:00:00.000Z"
    assert first["source"] == "okta"
    assert first["kind"] == "events"
    assert first["level_or_status"] == "SUCCESS"
    assert first["message"] == "User login to Okta"
    assert first["value"] == 1
    assert first["labels"] == {
        "eventType": "user.session.start",
        "actor": "alice@example.com",
        "client_ip": "203.0.113.7",
        "outcome": "SUCCESS",
    }
    assert first["raw"]["uuid"] == "evt-1"
    assert rows[1]["level_or_status"] == "FAILURE"


def test_query_passes_spec_params(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, [])])
    _src(transport).query(
        {"since": "2026-06-01", "until": "2026-06-12", "filter": 'eventType eq "x"'}
    )
    sent = transport.calls[0]["params"]
    assert sent["since"] == "2026-06-01"
    assert sent["until"] == "2026-06-12"
    assert sent["filter"] == 'eventType eq "x"'


# -- auth from env --------------------------------------------------------


def test_auth_header_from_env(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, [])])
    _src(transport).query({})
    assert transport.calls[0]["headers"]["Authorization"] == f"SSWS {token}"


def test_missing_env_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    transport = RecordingTransport([FakeResponse(200, [])])
    with pytest.raises(RuntimeError, match="unset or empty"):
        _src(transport).query({})


def test_token_not_in_any_url_or_params(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, CANNED_LOG)])
    _src(transport).query({})
    call = transport.calls[0]
    assert token not in call["url"]
    assert token not in str(call["params"])


# -- pagination (Link header) ---------------------------------------------


def test_pagination_follows_link_header(token: str) -> None:
    page1 = FakeResponse(
        200,
        [CANNED_LOG[0]],
        headers={
            "Link": (
                '<https://example.okta.com/api/v1/logs?after=cur2>; rel="next", '
                '<https://example.okta.com/api/v1/logs>; rel="self"'
            )
        },
    )
    page2 = FakeResponse(200, [CANNED_LOG[1]])  # no Link -> stop
    transport = RecordingTransport([page1, page2])
    rows = _src(transport).query({})
    assert len(rows) == 2
    assert len(transport.calls) == 2
    # The cursor from the Link header propagated into page-2 params.
    assert transport.calls[1]["params"].get("after") == "cur2"


def test_pagination_bounded_by_max_pages(token: str) -> None:
    looping = FakeResponse(
        200,
        [CANNED_LOG[0]],
        headers={"Link": '<https://example.okta.com/api/v1/logs?after=x>; rel="next"'},
    )
    # Always returns a next-link; max_pages must cap the loop.
    transport = RecordingTransport([looping] * 50)
    rows = _src(transport, max_pages=3).query({})
    assert len(transport.calls) == 3
    assert len(rows) == 3


# -- SSRF -----------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "http://127.0.0.1",
        "https://localhost",
        "http://10.0.0.5",
        "http://169.254.169.254",
        "http://192.168.1.1",
        "http://[::1]",
    ],
)
def test_ssrf_rejects_private(bad: str) -> None:
    with pytest.raises(ValueError, match=r"private|loopback"):
        OktaSource(
            {"org_url": bad, "token_env": TOKEN_ENV},
            transport=lambda *a, **k: None,
        )


def test_ssrf_allow_private_opt_in(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, [])])
    src = OktaSource(
        {"org_url": "http://127.0.0.1", "token_env": TOKEN_ENV, "allow_private": True},
        transport=transport,
    )
    assert src.query({}) == []


def test_rejects_non_http_scheme() -> None:
    with pytest.raises(ValueError, match="scheme"):
        OktaSource(
            {"org_url": "file:///etc/passwd", "token_env": TOKEN_ENV},
            transport=lambda *a, **k: None,
        )


# -- health ---------------------------------------------------------------


def test_health_ok(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, [])])
    h = _src(transport).health()
    assert h["ok"] is True
    assert "detail" in h


def test_health_not_ok_on_4xx(token: str) -> None:
    transport = RecordingTransport([FakeResponse(403, {"error": "forbidden"})])
    h = _src(transport).health()
    assert h["ok"] is False
    assert h["detail"] == "okta HTTP 403"


def test_health_never_raises_on_transport_error(token: str) -> None:
    src = _src(_raising_transport)
    h = src.health()
    assert h["ok"] is False
    assert h["detail"] == "health check failed"


def test_query_raises_on_http_error(token: str) -> None:
    transport = RecordingTransport([FakeResponse(500, {})])
    with pytest.raises(RuntimeError, match="HTTP 500"):
        _src(transport).query({})


def test_timeout_is_bounded(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, [])])
    _src(transport, timeout=5.0).query({})
    assert transport.calls[0]["timeout"] == 5.0
