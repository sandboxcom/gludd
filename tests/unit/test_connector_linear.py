"""Unit tests for the self-contained Linear issue-tracking connector.

All HTTP is mocked through an injected fake transport — no network access.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.linear import LinearSource, LINEAR_GRAPHQL_URL

TEAM_ID = "team-uuid-abc"
TOKEN_ENV = "LINEAR_TEST_TOKEN"


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
        json: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> FakeResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "json": json,
                "timeout": timeout,
            }
        )
        if not self._responses:
            return FakeResponse(200, {})
        return self._responses.pop(0)


def _raising_transport(*args: Any, **kwargs: Any) -> FakeResponse:
    raise ConnectionError("boom")


CANNED_ISSUES = [
    {
        "id": "iss-1",
        "title": "Fix login bug",
        "description": "Users cannot authenticate.",
        "state": {"name": "In Progress"},
        "createdAt": "2026-06-12T10:00:00.000Z",
        "updatedAt": "2026-06-13T10:00:00.000Z",
        "assignee": {"name": "Alice"},
        "priority": 1,
    },
    {
        "id": "iss-2",
        "title": "Add rate limiting",
        "description": "Throttle API requests.",
        "state": {"name": "Todo"},
        "createdAt": "2026-06-12T11:00:00.000Z",
        "updatedAt": "2026-06-12T11:00:00.000Z",
        "assignee": {"name": "Bob"},
        "priority": 2,
    },
]


def _issue_response(
    nodes: list[dict[str, Any]],
    has_next: bool = False,
    end_cursor: str | None = None,
) -> dict[str, Any]:
    return {
        "data": {
            "team": {
                "issues": {
                    "nodes": nodes,
                    "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
                }
            }
        }
    }


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv(TOKEN_ENV, "lin-api-token-secret")
    return "lin-api-token-secret"


def _src(transport: Any, **extra: Any) -> LinearSource:
    cfg: dict[str, Any] = {"team_id": TEAM_ID, "token_env": TOKEN_ENV}
    cfg.update(extra)
    return LinearSource(cfg, transport=transport)


# -- contract / attributes ------------------------------------------------


def test_class_attrs() -> None:
    assert LinearSource.KIND == "tickets"
    src = LinearSource(
        {"team_id": TEAM_ID, "token_env": TOKEN_ENV}, transport=lambda *a, **k: None
    )
    assert src.name == "linear"


def test_requires_team_id() -> None:
    with pytest.raises(ValueError, match="team_id"):
        LinearSource({"token_env": TOKEN_ENV}, transport=lambda *a, **k: None)


def test_requires_token_env() -> None:
    with pytest.raises(ValueError, match="token_env"):
        LinearSource({"team_id": TEAM_ID}, transport=lambda *a, **k: None)


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
        LinearSource(
            {"team_id": TEAM_ID, "token_env": TOKEN_ENV, "graphql_url": bad},
            transport=lambda *a, **k: None,
        )


def test_rejects_non_http_scheme() -> None:
    with pytest.raises(ValueError, match="scheme"):
        LinearSource(
            {
                "team_id": TEAM_ID,
                "token_env": TOKEN_ENV,
                "graphql_url": "file:///etc/passwd",
            },
            transport=lambda *a, **k: None,
        )


# -- normalization --------------------------------------------------------


def test_query_normalizes(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, _issue_response(CANNED_ISSUES))])
    rows = _src(transport).query()

    assert len(rows) == 2
    first = rows[0]
    assert first["ts"] == "2026-06-12T10:00:00.000Z"
    assert first["source"] == "linear"
    assert first["kind"] == "tickets"
    assert first["level_or_status"] == "In Progress"
    assert first["message"] == "Fix login bug"
    assert first["value"] == 1
    assert first["labels"] == {
        "linear_id": "iss-1",
        "state": "In Progress",
        "assignee": "Alice",
        "priority": 1,
    }
    assert first["raw"]["id"] == "iss-1"
    assert rows[1]["level_or_status"] == "Todo"


# -- auth from env --------------------------------------------------------


def test_auth_header_raw_no_bearer(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, _issue_response([]))])
    _src(transport).query()
    assert transport.calls[0]["headers"]["Authorization"] == token


def test_missing_env_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    transport = RecordingTransport([FakeResponse(200, _issue_response([]))])
    with pytest.raises(RuntimeError, match="unset or empty"):
        _src(transport).query()


def test_token_not_in_url(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, _issue_response(CANNED_ISSUES))])
    _src(transport).query()
    call = transport.calls[0]
    assert token not in call["url"]
    assert token not in str(call["json"])


# -- GraphQL POST ---------------------------------------------------------


def test_uses_graphql_post(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, _issue_response([]))])
    _src(transport).query()
    assert transport.calls[0]["method"] == "POST"
    assert transport.calls[0]["url"] == LINEAR_GRAPHQL_URL
    assert "query" in transport.calls[0]["json"]
    assert "Issues($teamId:" in transport.calls[0]["json"]["query"]
    assert transport.calls[0]["json"]["variables"]["teamId"] == TEAM_ID


# -- cursor pagination ----------------------------------------------------


def test_pagination_follows_cursor(token: str) -> None:
    page1 = FakeResponse(
        200, _issue_response([CANNED_ISSUES[0]], has_next=True, end_cursor="cursor-abc")
    )
    page2 = FakeResponse(
        200, _issue_response([CANNED_ISSUES[1]], has_next=False)
    )
    transport = RecordingTransport([page1, page2])
    rows = _src(transport).query()
    assert len(rows) == 2
    assert len(transport.calls) == 2
    assert transport.calls[1]["json"]["variables"].get("after") == "cursor-abc"


def test_pagination_bounded_by_max_pages(token: str) -> None:
    looping = FakeResponse(
        200,
        _issue_response([CANNED_ISSUES[0]], has_next=True, end_cursor="x"),
    )
    transport = RecordingTransport([looping] * 50)
    rows = _src(transport, max_pages=3).query()
    assert len(transport.calls) == 3
    assert len(rows) == 3


# -- health ---------------------------------------------------------------


def test_health_ok(token: str) -> None:
    transport = RecordingTransport(
        [FakeResponse(200, {"data": {"viewer": {"id": "u1", "name": "Test"}}})]
    )
    h = _src(transport).health()
    assert h["ok"] is True
    assert "detail" in h


def test_health_not_ok_on_4xx(token: str) -> None:
    transport = RecordingTransport([FakeResponse(403, {"error": "forbidden"})])
    h = _src(transport).health()
    assert h["ok"] is False
    assert h["detail"] == "linear HTTP 403"


def test_health_never_raises_on_transport_error(token: str) -> None:
    src = _src(_raising_transport)
    h = src.health()
    assert h["ok"] is False
    assert h["detail"] == "health check failed"


def test_health_uses_viewer_query(token: str) -> None:
    transport = RecordingTransport(
        [FakeResponse(200, {"data": {"viewer": {"id": "u1", "name": "Test"}}})]
    )
    _src(transport).health()
    assert transport.calls[0]["method"] == "POST"
    assert transport.calls[0]["json"]["query"] == "query { viewer { id name } }"


# -- error handling -------------------------------------------------------


def test_query_raises_on_http_error(token: str) -> None:
    transport = RecordingTransport([FakeResponse(500, {})])
    with pytest.raises(RuntimeError, match="HTTP 500"):
        _src(transport).query()


def test_timeout_is_bounded(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, _issue_response([]))])
    _src(transport, timeout=5.0).query()
    assert transport.calls[0]["timeout"] == 5.0
