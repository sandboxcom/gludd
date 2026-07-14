"""Unit tests for the self-contained Zendesk ticket connector.

All HTTP is mocked through an injected fake transport — no network access.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.zendesk import ZendeskSource

SUBDOMAIN = "mycompany"
EMAIL_ENV = "ZENDESK_TEST_EMAIL"
TOKEN_ENV = "ZENDESK_TEST_TOKEN"


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._body = body if body is not None else {}
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
            return FakeResponse(200, {})
        return self._responses.pop(0)


def _raising_transport(*args: Any, **kwargs: Any) -> FakeResponse:
    raise ConnectionError("boom")


CANNED_TICKETS = [
    {
        "id": 42,
        "subject": "Cannot access dashboard",
        "description": "User reports 403 when loading /dashboard.",
        "status": "open",
        "priority": "urgent",
        "type": "incident",
        "requester_id": 1001,
        "assignee_id": 2001,
        "created_at": "2026-06-12T10:00:00Z",
        "updated_at": "2026-06-12T11:00:00Z",
    },
    {
        "id": 43,
        "subject": "Billing question",
        "description": "Customer wants to upgrade their plan.",
        "status": "pending",
        "priority": "normal",
        "type": "question",
        "requester_id": 1002,
        "assignee_id": 2002,
        "created_at": "2026-06-12T09:00:00Z",
        "updated_at": "2026-06-12T10:30:00Z",
    },
]


@pytest.fixture
def credentials(monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    monkeypatch.setenv(EMAIL_ENV, "agent@example.com")
    monkeypatch.setenv(TOKEN_ENV, "s3cr3t-zendesk-token")
    return ("agent@example.com", "s3cr3t-zendesk-token")


def _src(transport: Any, **extra: Any) -> ZendeskSource:
    cfg: dict[str, Any] = {
        "subdomain": SUBDOMAIN,
        "email_env": EMAIL_ENV,
        "token_env": TOKEN_ENV,
    }
    cfg.update(extra)
    return ZendeskSource(cfg, transport=transport)


# -- contract / attributes ------------------------------------------------


def test_class_attrs() -> None:
    assert ZendeskSource.KIND == "tickets"
    src = ZendeskSource(
        {"subdomain": SUBDOMAIN, "email_env": EMAIL_ENV, "token_env": TOKEN_ENV},
        transport=lambda *a, **k: None,
    )
    assert src.name == "zendesk"


def test_requires_subdomain() -> None:
    with pytest.raises(ValueError, match="subdomain"):
        ZendeskSource(
            {"email_env": EMAIL_ENV, "token_env": TOKEN_ENV},
            transport=lambda *a, **k: None,
        )


def test_requires_email_env() -> None:
    with pytest.raises(ValueError, match="email_env"):
        ZendeskSource(
            {"subdomain": SUBDOMAIN, "token_env": TOKEN_ENV},
            transport=lambda *a, **k: None,
        )


def test_requires_token_env() -> None:
    with pytest.raises(ValueError, match="token_env"):
        ZendeskSource(
            {"subdomain": SUBDOMAIN, "email_env": EMAIL_ENV},
            transport=lambda *a, **k: None,
        )


# -- SSRF -----------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_subdomain",
    [
        "127.0.0.1",
        "localhost",
        "10.0.0.5",
        "169.254.169.254",
        "192.168.1.1",
    ],
)
def test_ssrf_rejects_private(bad_subdomain: str) -> None:
    with pytest.raises(ValueError, match=r"private|loopback"):
        ZendeskSource(
            {"subdomain": bad_subdomain, "email_env": EMAIL_ENV, "token_env": TOKEN_ENV},
            transport=lambda *a, **k: None,
        )


def test_ssrf_allow_private_opt_in(credentials: tuple[str, str]) -> None:
    transport = RecordingTransport([FakeResponse(200, {"tickets": []})])
    src = ZendeskSource(
        {
            "subdomain": "127.0.0.1",
            "email_env": EMAIL_ENV,
            "token_env": TOKEN_ENV,
            "allow_private": True,
        },
        transport=transport,
    )
    assert src.query({}) == []


def test_ssrf_rejects_non_http_scheme() -> None:
    with pytest.raises(ValueError, match="scheme"):
        ZendeskSource(
            {"subdomain": "ftp.example", "email_env": EMAIL_ENV, "token_env": TOKEN_ENV},
            transport=lambda *a, **k: None,
        )


# -- normalization --------------------------------------------------------


def test_query_normalizes(credentials: tuple[str, str]) -> None:
    transport = RecordingTransport(
        [FakeResponse(200, {"tickets": CANNED_TICKETS})]
    )
    rows = _src(transport).query({"status": "open"})

    assert len(rows) == 2
    first = rows[0]
    assert first["ts"] == "2026-06-12T11:00:00Z"
    assert first["source"] == "zendesk"
    assert first["kind"] == "tickets"
    assert first["level_or_status"] == "open"
    assert first["message"] == "Cannot access dashboard"
    assert first["value"] == 42
    assert first["labels"] == {
        "subject": "Cannot access dashboard",
        "priority": "urgent",
        "type": "incident",
        "requester_id": 1001,
        "assignee_id": 2001,
        "status": "open",
    }
    assert first["raw"]["id"] == 42
    assert rows[1]["level_or_status"] == "pending"


def test_query_passes_spec_params(credentials: tuple[str, str]) -> None:
    transport = RecordingTransport([FakeResponse(200, {"tickets": []})])
    _src(transport).query(
        {"sort_by": "updated_at", "sort_order": "desc", "status": "open"}
    )
    sent = transport.calls[0]["params"]
    assert sent["sort_by"] == "updated_at"
    assert sent["sort_order"] == "desc"
    assert sent["status"] == "open"


# -- query error handling -------------------------------------------------


def test_query_raises_on_http_error(credentials: tuple[str, str]) -> None:
    transport = RecordingTransport([FakeResponse(500, {})])
    with pytest.raises(RuntimeError, match="HTTP 500"):
        _src(transport).query({})


# -- auth -----------------------------------------------------------------


def test_auth_header_present(credentials: tuple[str, str]) -> None:
    transport = RecordingTransport([FakeResponse(200, {"tickets": []})])
    _src(transport).query({})
    auth = transport.calls[0]["headers"]["Authorization"]
    assert auth.startswith("Basic ")
    # Decode and verify the base64 contents
    import base64
    decoded = base64.b64decode(auth.split(" ", 1)[1]).decode("utf-8")
    assert decoded == "agent@example.com/token:s3cr3t-zendesk-token"


def test_missing_email_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, "tok")
    monkeypatch.delenv(EMAIL_ENV, raising=False)
    transport = RecordingTransport([FakeResponse(200, {"tickets": []})])
    with pytest.raises(RuntimeError, match="unset or empty"):
        _src(transport).query({})


def test_missing_token_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EMAIL_ENV, "a@b.com")
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    transport = RecordingTransport([FakeResponse(200, {"tickets": []})])
    with pytest.raises(RuntimeError, match="unset or empty"):
        _src(transport).query({})


def test_auth_not_in_url_or_params(credentials: tuple[str, str]) -> None:
    transport = RecordingTransport([FakeResponse(200, {"tickets": CANNED_TICKETS})])
    _src(transport).query({})
    call = transport.calls[0]
    assert "s3cr3t-zendesk-token" not in call["url"]
    assert "s3cr3t-zendesk-token" not in str(call["params"])


# -- pagination -----------------------------------------------------------


def test_pagination_follows_next_link(credentials: tuple[str, str]) -> None:
    page1 = FakeResponse(
        200,
        {
            "tickets": [CANNED_TICKETS[0]],
            "links": {"next": "https://mycompany.zendesk.com/api/v2/tickets.json?page=2"},
        },
    )
    page2 = FakeResponse(200, {"tickets": [CANNED_TICKETS[1]]})
    transport = RecordingTransport([page1, page2])
    rows = _src(transport, max_items=10).query({})
    assert len(rows) == 2
    assert len(transport.calls) == 2


def test_pagination_bounded_by_max_items(credentials: tuple[str, str]) -> None:
    looping = FakeResponse(
        200,
        {
            "tickets": [CANNED_TICKETS[0]],
            "links": {"next": "https://mycompany.zendesk.com/api/v2/tickets.json?page=2"},
        },
    )
    transport = RecordingTransport([looping] * 50)
    rows = _src(transport, max_items=3).query({})
    assert len(rows) == 3


# -- health ---------------------------------------------------------------


def test_health_ok(credentials: tuple[str, str]) -> None:
    transport = RecordingTransport([FakeResponse(200, {})])
    h = _src(transport).health()
    assert h["ok"] is True
    assert "detail" in h


def test_health_not_ok_on_4xx(credentials: tuple[str, str]) -> None:
    transport = RecordingTransport([FakeResponse(403, {"error": "forbidden"})])
    h = _src(transport).health()
    assert h["ok"] is False
    assert h["detail"] == "zendesk HTTP 403"


def test_health_never_raises(credentials: tuple[str, str]) -> None:
    src = _src(_raising_transport)
    h = src.health()
    assert h["ok"] is False
    assert h["detail"] == "health check failed"


# -- timeout --------------------------------------------------------------


def test_timeout_is_bounded(credentials: tuple[str, str]) -> None:
    transport = RecordingTransport([FakeResponse(200, {"tickets": []})])
    _src(transport, timeout=5.0).query({})
    assert transport.calls[0]["timeout"] == 5.0
