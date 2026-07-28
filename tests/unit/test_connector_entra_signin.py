"""Unit tests for the self-contained Entra ID sign-in connector.

All HTTP is mocked through an injected fake transport — no network access. The
Graph token is supplied via env (acquired externally); no OAuth is exercised.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.entra_signin import EntraSignInSource

TOKEN_ENV = "ENTRA_TEST_TOKEN"


class FakeResponse:
    def __init__(self, status_code: int = 200, body: Any = None) -> None:
        self.status_code = status_code
        self._body = body if body is not None else {"value": []}

    def json(self) -> Any:
        return self._body


class RecordingTransport:
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
            return FakeResponse(200, {"value": []})
        return self._responses.pop(0)


def _raising_transport(*args: Any, **kwargs: Any) -> FakeResponse:
    raise OSError("network down")


CANNED = [
    {
        "id": "si-1",
        "createdDateTime": "2026-06-12T08:00:00Z",
        "appDisplayName": "Office 365",
        "userPrincipalName": "alice@example.com",
        "ipAddress": "203.0.113.20",
        "clientAppUsed": "Browser",
        "conditionalAccessStatus": "success",
        "status": {"errorCode": 0, "failureReason": "Other."},
    },
    {
        "id": "si-2",
        "createdDateTime": "2026-06-12T08:05:00Z",
        "appDisplayName": "Azure Portal",
        "userPrincipalName": "bob@example.com",
        "ipAddress": "203.0.113.21",
        "clientAppUsed": "Mobile Apps and Desktop clients",
        "conditionalAccessStatus": "failure",
        "status": {"errorCode": 50126, "failureReason": "Invalid creds"},
    },
]


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv(TOKEN_ENV, "graph-bearer-token")
    return "graph-bearer-token"


def _src(transport: Any, **extra: Any) -> EntraSignInSource:
    cfg: dict[str, Any] = {"token_env": TOKEN_ENV}
    cfg.update(extra)
    return EntraSignInSource(cfg, transport=transport)


def _body(records: list[dict[str, Any]], next_link: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"value": records}
    if next_link:
        out["@odata.nextLink"] = next_link
    return out


# -- contract / attributes ------------------------------------------------


def test_class_attrs() -> None:
    assert EntraSignInSource.KIND == "events"
    src = EntraSignInSource({"token_env": TOKEN_ENV}, transport=lambda *a, **k: None)
    assert src.name == "entra_signin"


def test_legacy_class_name_preserves_token_contract(token: str) -> None:
    from general_ludd.connectors.entra_signin import EntraSigninSource

    transport = RecordingTransport([FakeResponse(200, _body(CANNED[:1]))])
    source = EntraSigninSource({"token_env": TOKEN_ENV}, transport=transport)

    rows = source.query({})

    assert rows[0]["raw"]["id"] == "si-1"
    assert transport.calls[0]["headers"]["Authorization"] == f"Bearer {token}"


def test_requires_token_env() -> None:
    with pytest.raises(ValueError, match="token_env"):
        EntraSignInSource({}, transport=lambda *a, **k: None)


def test_default_endpoint(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, _body([]))])
    _src(transport).query({})
    assert transport.calls[0]["url"] == (
        "https://graph.microsoft.com/v1.0/auditLogs/signIns"
    )


# -- normalization --------------------------------------------------------


def test_query_normalizes_records(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, _body(CANNED))])
    rows = _src(transport).query({})
    assert len(rows) == 2

    ok = rows[0]
    assert ok["ts"] == "2026-06-12T08:00:00Z"
    assert ok["source"] == "entra_signin"
    assert ok["kind"] == "events"
    assert ok["level_or_status"] == "success"  # errorCode == 0
    assert ok["message"] == "Office 365"
    assert ok["value"] == 1
    assert ok["labels"] == {
        "userPrincipalName": "alice@example.com",
        "ipAddress": "203.0.113.20",
        "clientAppUsed": "Browser",
        "conditionalAccessStatus": "success",
    }
    assert ok["raw"]["id"] == "si-1"

    bad = rows[1]
    assert bad["level_or_status"] == "failure"  # errorCode != 0
    assert bad["message"] == "Azure Portal"


def test_status_label_handles_missing_status(token: str) -> None:
    rec = {"id": "x", "createdDateTime": "t", "appDisplayName": "A"}
    transport = RecordingTransport([FakeResponse(200, _body([rec]))])
    rows = _src(transport).query({})
    assert rows[0]["level_or_status"] == "failure"


def test_filter_passed_as_odata(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, _body([]))])
    _src(transport).query({"filter": "userPrincipalName eq 'alice@example.com'"})
    assert (
        transport.calls[0]["params"]["$filter"]
        == "userPrincipalName eq 'alice@example.com'"
    )


# -- auth from env --------------------------------------------------------


def test_auth_header_from_env(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, _body([]))])
    _src(transport).query({})
    assert transport.calls[0]["headers"]["Authorization"] == f"Bearer {token}"


def test_missing_env_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    transport = RecordingTransport([FakeResponse(200, _body([]))])
    with pytest.raises(RuntimeError, match="unset or empty"):
        _src(transport).query({})


def test_token_not_leaked_into_request(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, _body(CANNED))])
    _src(transport).query({})
    call = transport.calls[0]
    assert token not in call["url"]
    assert token not in str(call["params"])


# -- pagination (@odata.nextLink) -----------------------------------------


def test_pagination_follows_next_link(token: str) -> None:
    nxt = "https://graph.microsoft.com/v1.0/auditLogs/signIns?$skiptoken=abc"
    p1 = FakeResponse(200, _body([CANNED[0]], next_link=nxt))
    p2 = FakeResponse(200, _body([CANNED[1]]))  # no nextLink -> stop
    transport = RecordingTransport([p1, p2])
    rows = _src(transport).query({})
    assert len(rows) == 2
    assert len(transport.calls) == 2
    assert transport.calls[1]["params"].get("$skiptoken") == "abc"


def test_pagination_bounded_by_max_pages(token: str) -> None:
    nxt = "https://graph.microsoft.com/v1.0/auditLogs/signIns?$skiptoken=loop"
    pages = [FakeResponse(200, _body([CANNED[0]], next_link=nxt)) for _ in range(99)]
    transport = RecordingTransport(pages)
    rows = _src(transport, max_pages=4).query({})
    assert len(transport.calls) == 4
    assert len(rows) == 4


# -- SSRF -----------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "http://127.0.0.1/v1.0/auditLogs/signIns",
        "https://localhost/signIns",
        "http://10.0.0.1/signIns",
        "http://169.254.169.254/metadata",  # cloud metadata IP (regression)
        "http://[::1]/signIns",
        # Metadata NAME coverage added by the canonical shared guard.
        "http://metadata.google.internal/signIns",
    ],
)
def test_ssrf_rejects_private_base_url(bad: str) -> None:
    with pytest.raises(ValueError, match=r"private|loopback"):
        EntraSignInSource(
            {"token_env": TOKEN_ENV, "base_url": bad},
            transport=lambda *a, **k: None,
        )


def test_ssrf_allow_private_opt_in(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, _body([]))])
    src = EntraSignInSource(
        {
            "token_env": TOKEN_ENV,
            "base_url": "http://127.0.0.1/signIns",
            "allow_private": True,
        },
        transport=transport,
    )
    assert src.query({}) == []


# -- health ---------------------------------------------------------------


def test_health_ok(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, _body([]))])
    h = _src(transport).health()
    assert h["ok"] is True


def test_health_not_ok_on_4xx(token: str) -> None:
    transport = RecordingTransport([FakeResponse(403, {})])
    h = _src(transport).health()
    assert h["ok"] is False
    assert h["detail"] == "entra HTTP 403"


def test_health_never_raises(token: str) -> None:
    h = _src(_raising_transport).health()
    assert h["ok"] is False
    assert h["detail"] == "health check failed"


def test_query_raises_on_http_error(token: str) -> None:
    transport = RecordingTransport([FakeResponse(429, {})])
    with pytest.raises(RuntimeError, match="HTTP 429"):
        _src(transport).query({})


def test_timeout_is_bounded(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, _body([]))])
    _src(transport, timeout=12.0).query({})
    assert transport.calls[0]["timeout"] == 12.0
