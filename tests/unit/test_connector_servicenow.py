"""Unit tests for the self-contained ServiceNow Incident connector.

All HTTP is mocked through an injected fake transport — no network access.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.servicenow import ServiceNowSource

INSTANCE = "dev12345"
USER_ENV = "SNOW_USER"
PASS_ENV = "SNOW_PASS"


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
        auth: tuple[str, str] | None = None,
    ) -> FakeResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "params": params or {},
                "timeout": timeout,
                "auth": auth,
            }
        )
        if not self._responses:
            return FakeResponse(200, {"result": []})
        return self._responses.pop(0)


def _raising_transport(*args: Any, **kwargs: Any) -> FakeResponse:
    raise ConnectionError("boom")


CANNED_INCIDENTS = {
    "result": [
        {
            "sys_id": "abc123",
            "number": "INC0000001",
            "short_description": "Email server down",
            "priority": "1",
            "state": "2",
            "opened_at": "2026-06-12T10:00:00",
            "caller_id": {"value": "jdoe"},
            "assignment_group": {"value": "Network Support"},
        },
        {
            "sys_id": "def456",
            "number": "INC0000002",
            "short_description": "VPN connectivity issue",
            "priority": "2",
            "state": "1",
            "opened_at": "2026-06-12T11:00:00",
            "caller_id": {"value": "asmith"},
            "assignment_group": {"value": "Security"},
        },
    ]
}


@pytest.fixture
def credentials(monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    monkeypatch.setenv(USER_ENV, "admin")
    monkeypatch.setenv(PASS_ENV, "secret-pass")
    return ("admin", "secret-pass")


def _src(transport: Any, **extra: Any) -> ServiceNowSource:
    cfg: dict[str, Any] = {
        "instance": INSTANCE,
        "user_env": USER_ENV,
        "pass_env": PASS_ENV,
    }
    cfg.update(extra)
    return ServiceNowSource(cfg, transport=transport)


# -- contract / attributes ------------------------------------------------


def test_class_attrs() -> None:
    assert ServiceNowSource.KIND == "tickets"
    src = ServiceNowSource(
        {"instance": INSTANCE, "user_env": USER_ENV, "pass_env": PASS_ENV},
        transport=lambda *a, **k: None,
    )
    assert src.name == "servicenow"


def test_requires_instance() -> None:
    with pytest.raises(ValueError, match="instance"):
        ServiceNowSource(
            {"user_env": USER_ENV, "pass_env": PASS_ENV},
            transport=lambda *a, **k: None,
        )


def test_requires_user_env() -> None:
    with pytest.raises(ValueError, match="user_env"):
        ServiceNowSource(
            {"instance": INSTANCE, "pass_env": PASS_ENV},
            transport=lambda *a, **k: None,
        )


def test_requires_pass_env() -> None:
    with pytest.raises(ValueError, match="pass_env"):
        ServiceNowSource(
            {"instance": INSTANCE, "user_env": USER_ENV},
            transport=lambda *a, **k: None,
        )


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
        ServiceNowSource(
            {"instance": bad, "user_env": USER_ENV, "pass_env": PASS_ENV},
            transport=lambda *a, **k: None,
        )


def test_ssrf_allow_private_opt_in(credentials: tuple[str, str]) -> None:
    transport = RecordingTransport([FakeResponse(200, {"result": []})])
    src = ServiceNowSource(
        {
            "instance": "127.0.0.1",
            "user_env": USER_ENV,
            "pass_env": PASS_ENV,
            "allow_private": True,
        },
        transport=transport,
    )
    assert src.query({}) == []


def test_rejects_non_http_scheme() -> None:
    with pytest.raises(ValueError, match="scheme"):
        ServiceNowSource(
            {"instance": "file:///etc/passwd", "user_env": USER_ENV, "pass_env": PASS_ENV},
            transport=lambda *a, **k: None,
        )


# -- normalization --------------------------------------------------------


def test_query_normalizes(credentials: tuple[str, str]) -> None:
    transport = RecordingTransport([FakeResponse(200, CANNED_INCIDENTS)])
    rows = _src(transport).query({"sysparm_query": "priority=1"})

    assert len(rows) == 2
    first = rows[0]
    assert first["ts"] == "2026-06-12T10:00:00"
    assert first["source"] == "servicenow"
    assert first["kind"] == "tickets"
    assert first["level_or_status"] == "in_progress"
    assert first["message"] == "Email server down"
    assert first["value"] == 1
    labels = first["labels"]
    assert labels["sys_id"] == "abc123"
    assert labels["number"] == "INC0000001"
    assert labels["priority"] == "1"
    assert labels["state"] == "2"
    assert labels["caller"] == "jdoe"
    assert labels["assignment_group"] == "Network Support"
    assert first["raw"]["sys_id"] == "abc123"
    assert rows[1]["level_or_status"] == "new"


def test_query_passes_spec_params(credentials: tuple[str, str]) -> None:
    transport = RecordingTransport([FakeResponse(200, {"result": []})])
    _src(transport).query(
        {
            "sysparm_query": "active=true",
            "sysparm_limit": "50",
            "sysparm_fields": "number,short_description",
        }
    )
    sent = transport.calls[0]["params"]
    assert sent["sysparm_query"] == "active=true"
    assert sent["sysparm_limit"] == "50"
    assert sent["sysparm_fields"] == "number,short_description"


# -- auth from env --------------------------------------------------------


def test_auth_from_env(credentials: tuple[str, str]) -> None:
    transport = RecordingTransport([FakeResponse(200, {"result": []})])
    _src(transport).query({})
    assert transport.calls[0]["auth"] == ("admin", "secret-pass")


def test_missing_user_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(USER_ENV, raising=False)
    monkeypatch.setenv(PASS_ENV, "p")
    transport = RecordingTransport([FakeResponse(200, {"result": []})])
    with pytest.raises(RuntimeError, match="unset or empty"):
        _src(transport).query({})


def test_missing_pass_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(USER_ENV, "u")
    monkeypatch.delenv(PASS_ENV, raising=False)
    transport = RecordingTransport([FakeResponse(200, {"result": []})])
    with pytest.raises(RuntimeError, match="unset or empty"):
        _src(transport).query({})


def test_credentials_not_in_url_or_params(credentials: tuple[str, str]) -> None:
    transport = RecordingTransport([FakeResponse(200, CANNED_INCIDENTS)])
    _src(transport).query({})
    call = transport.calls[0]
    assert "admin" not in call["url"]
    assert "secret-pass" not in call["url"]
    assert "admin" not in str(call["params"])
    assert "secret-pass" not in str(call["params"])


# -- pagination (Link header) ---------------------------------------------


def test_pagination_follows_link_header(credentials: tuple[str, str]) -> None:
    page1 = FakeResponse(
        200,
        {"result": [CANNED_INCIDENTS["result"][0]]},
        headers={
            "Link": (
                '<https://dev12345.service-now.com'
                '/api/now/table/incident?sysparm_offset=1>; rel="next", '
                '<https://dev12345.service-now.com'
                '/api/now/table/incident>; rel="self"'
            )
        },
    )
    page2 = FakeResponse(200, {"result": [CANNED_INCIDENTS["result"][1]]})
    transport = RecordingTransport([page1, page2])
    rows = _src(transport).query({})
    assert len(rows) == 2
    assert len(transport.calls) == 2
    assert transport.calls[1]["params"].get("sysparm_offset") == "1"


def test_pagination_bounded_by_max_pages(credentials: tuple[str, str]) -> None:
    looping = FakeResponse(
        200,
        {"result": [CANNED_INCIDENTS["result"][0]]},
        headers={
            "Link": '<https://dev12345.service-now.com'
                    '/api/now/table/incident?sysparm_offset=1>; rel="next"'
        },
    )
    transport = RecordingTransport([looping] * 50)
    rows = _src(transport, max_pages=3).query({})
    assert len(transport.calls) == 3
    assert len(rows) == 3


# -- health ---------------------------------------------------------------


def test_health_ok(credentials: tuple[str, str]) -> None:
    transport = RecordingTransport([FakeResponse(200, {"result": []})])
    h = _src(transport).health()
    assert h["ok"] is True
    assert "detail" in h


def test_health_not_ok_on_4xx(credentials: tuple[str, str]) -> None:
    transport = RecordingTransport([FakeResponse(403, {"error": "forbidden"})])
    h = _src(transport).health()
    assert h["ok"] is False
    assert h["detail"] == "servicenow HTTP 403"


def test_health_never_raises(credentials: tuple[str, str]) -> None:
    src = _src(_raising_transport)
    h = src.health()
    assert h["ok"] is False
    assert h["detail"] == "health check failed"


def test_query_raises_on_http_error(credentials: tuple[str, str]) -> None:
    transport = RecordingTransport([FakeResponse(500, {})])
    with pytest.raises(RuntimeError, match="HTTP 500"):
        _src(transport).query({})


def test_timeout_is_bounded(credentials: tuple[str, str]) -> None:
    transport = RecordingTransport([FakeResponse(200, {"result": []})])
    _src(transport, timeout=5.0).query({})
    assert transport.calls[0]["timeout"] == 5.0
