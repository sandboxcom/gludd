"""Unit tests for the self-contained Monday.com connector.

All HTTP is mocked through an injected fake transport — no network access.
"""

from __future__ import annotations

from typing import Any

import pytest

from general_ludd.connectors.monday import MondaySource

TOKEN_ENV = "MONDAY_TEST_TOKEN"
API_URL = "https://api.monday.com/v2"


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
        json: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> FakeResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "params": params or {},
                "json": json or {},
                "timeout": timeout,
            }
        )
        if not self._responses:
            return FakeResponse(200, {})
        return self._responses.pop(0)


def _raising_transport(*args: Any, **kwargs: Any) -> FakeResponse:
    raise ConnectionError("boom")


CANNED_BOARD_RESPONSE = {
    "data": {
        "boards": [
            {
                "items_page": {
                    "cursor": None,
                    "items": [
                        {
                            "id": "123",
                            "name": "Fix login bug",
                            "created_at": "2026-07-01T10:00:00Z",
                            "updated_at": "2026-07-12T14:00:00Z",
                            "state": "active",
                            "group": {"id": "topics", "title": "Sprint 1"},
                            "column_values": [],
                        },
                        {
                            "id": "456",
                            "name": "Add dark mode",
                            "created_at": "2026-07-02T08:00:00Z",
                            "updated_at": "2026-07-11T09:00:00Z",
                            "state": "done",
                            "group": {"id": "features", "title": "Sprint 2"},
                            "column_values": [],
                        },
                    ],
                }
            }
        ]
    }
}


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv(TOKEN_ENV, "s3cr3t-monday-token")
    return "s3cr3t-monday-token"


def _src(transport: Any, **extra: Any) -> MondaySource:
    cfg: dict[str, Any] = {"board_ids": [123], "token_env": TOKEN_ENV}
    cfg.update(extra)
    return MondaySource(cfg, transport=transport)


# -- contract / attributes ------------------------------------------------


def test_class_attrs() -> None:
    assert MondaySource.KIND == "tasks"
    src = MondaySource(
        {"board_ids": [123], "token_env": TOKEN_ENV}, transport=lambda *a, **k: None
    )
    assert src.name == "monday"


def test_requires_board_ids() -> None:
    with pytest.raises(ValueError, match="board_ids"):
        MondaySource({"token_env": TOKEN_ENV}, transport=lambda *a, **k: None)

    with pytest.raises(ValueError, match="board_ids"):
        MondaySource(
            {"board_ids": [], "token_env": TOKEN_ENV}, transport=lambda *a, **k: None
        )


def test_requires_token_env() -> None:
    with pytest.raises(ValueError, match="token_env"):
        MondaySource({"board_ids": [123]}, transport=lambda *a, **k: None)


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
        MondaySource(
            {"board_ids": [1], "token_env": TOKEN_ENV, "mock_url": bad},
            transport=lambda *a, **k: None,
        )


# -- normalization --------------------------------------------------------


def test_query_normalizes(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, CANNED_BOARD_RESPONSE)])
    rows = _src(transport).query()

    assert len(rows) == 2
    first = rows[0]
    assert first["ts"] == "2026-07-12T14:00:00Z"
    assert first["source"] == "monday"
    assert first["kind"] == "tasks"
    assert first["level_or_status"] == "active"
    assert first["message"] == "Fix login bug"
    assert first["value"] == 1
    assert first["labels"] == {
        "board_id": 123,
        "item_id": "123",
        "group": "Sprint 1",
    }
    assert first["raw"]["id"] == "123"

    second = rows[1]
    assert second["level_or_status"] == "done"
    assert second["message"] == "Add dark mode"


def test_query_raises_on_http_error(token: str) -> None:
    transport = RecordingTransport([FakeResponse(500, {})])
    with pytest.raises(RuntimeError, match="HTTP 500"):
        _src(transport).query({})


def test_uses_graphql_post(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, CANNED_BOARD_RESPONSE)])
    _src(transport).query({})
    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == API_URL
    assert "query" in call["json"]
    assert "items_page" in call["json"]["query"]


def test_query_follows_items_page_cursor(token: str) -> None:
    first_item = CANNED_BOARD_RESPONSE["data"]["boards"][0]["items_page"]["items"][0]
    second_item = CANNED_BOARD_RESPONSE["data"]["boards"][0]["items_page"]["items"][1]
    first_page = {
        "data": {
            "boards": [
                {
                    "items_page": {
                        "cursor": "next-cursor",
                        "items": [first_item],
                    }
                }
            ]
        }
    }
    second_page = {
        "data": {
            "next_items_page": {
                "cursor": None,
                "items": [second_item],
            }
        }
    }
    transport = RecordingTransport(
        [FakeResponse(200, first_page), FakeResponse(200, second_page)]
    )

    rows = _src(transport).query({"limit": 1})

    assert [row["raw"]["id"] for row in rows] == ["123", "456"]
    assert len(transport.calls) == 2
    assert transport.calls[0]["json"]["variables"]["limit"] == 1
    assert transport.calls[1]["json"]["variables"]["cursor"] == "next-cursor"
    assert "next_items_page" in transport.calls[1]["json"]["query"]


def test_query_bounds_items_page_pagination(token: str) -> None:
    item = CANNED_BOARD_RESPONSE["data"]["boards"][0]["items_page"]["items"][0]
    first_page = {
        "data": {
            "boards": [
                {
                    "items_page": {
                        "cursor": "cursor-1",
                        "items": [item],
                    }
                }
            ]
        }
    }
    next_page = {
        "data": {
            "next_items_page": {
                "cursor": "cursor-2",
                "items": [dict(item, id="789")],
            }
        }
    }
    transport = RecordingTransport(
        [FakeResponse(200, first_page), FakeResponse(200, next_page)]
    )

    rows = _src(transport, max_pages=2).query({"limit": 1})

    assert len(transport.calls) == 2
    assert [row["raw"]["id"] for row in rows] == ["123", "789"]


# -- auth from env --------------------------------------------------------


def test_auth_header_from_env(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, CANNED_BOARD_RESPONSE)])
    _src(transport).query({})
    assert transport.calls[0]["headers"]["Authorization"] == token


def test_token_not_in_query_or_url(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, CANNED_BOARD_RESPONSE)])
    _src(transport).query({})
    call = transport.calls[0]
    assert token not in call["url"]
    assert token not in str(call["json"])


# -- health ---------------------------------------------------------------


def test_health_ok(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, {"data": {"me": {"id": 1, "name": "bot"}}})])
    h = _src(transport).health()
    assert h["ok"] is True
    assert "detail" in h


def test_health_not_ok_on_4xx(token: str) -> None:
    transport = RecordingTransport([FakeResponse(403, {"error": "forbidden"})])
    h = _src(transport).health()
    assert h["ok"] is False
    assert h["detail"] == "monday HTTP 403"


def test_health_never_raises_on_transport_error(token: str) -> None:
    src = _src(_raising_transport)
    h = src.health()
    assert h["ok"] is False
    assert h["detail"] == "health check failed"


# -- timeout -----------------------------------------------------------------


def test_timeout_is_bounded(token: str) -> None:
    transport = RecordingTransport([FakeResponse(200, CANNED_BOARD_RESPONSE)])
    _src(transport, timeout=5.0).query({})
    assert transport.calls[0]["timeout"] == 5.0
